"""The Line Sitter: one agent per person.

It holds that person's calendar and their constraints, written in their own
words. When the front desk offers a slot it answers accept or pass with a
one-sentence reason, or it asks its person one yes/no question.

What it must never do is invent a fact about its person. Three things
enforce that, and only the first is a prompt:

1. The model is told to use only the listed facts and to cite their ids.
2. Plain code checks that every cited id exists. No citation, no decision.
3. Plain code computes calendar overlaps itself. The model cannot accept a
   slot that overlaps something on the calendar; that turns into a question.

If the model does not answer, the sitter never accepts on its own. A hard
calendar overlap becomes a pass (code can see that without a model);
anything else becomes a question for the person.

The reason sentence stays on the person's side. The front desk is told only
"pass" and a coarse reason code, never what is on the calendar.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

import httpx

from . import llm
from .events import Transcript

DECISIONS = ("accept", "pass", "escalate")

SYSTEM_PROMPT = """You are a Line Sitter: an agent that holds one person's place in a line.
A front desk has offered your person a freed appointment slot. Decide for them.

Use ONLY the facts listed in the message. Each fact has an id (c1 is a constraint in the
person's own words, e1 is a calendar fact computed by code, t1 is what they wrote on the ticket).

Rules:
1. Never invent or assume anything about the person. If the decision depends on something that
   is not written in the facts (for example whether this visit counts as urgent, or whether an
   event can be moved), choose "escalate" and ask ONE question that can be answered yes or no.
2. Choose "pass" if a constraint or a calendar fact clearly rules the slot out.
3. Choose "accept" only if nothing rules the slot out and every constraint that applies is
   clearly met by the listed facts. A constraint about mornings does not apply to an afternoon slot.
4. "reason" is one plain sentence, 25 words or fewer, written to the person as "you". Say the fact you
   relied on in ordinary words. Do not put fact ids (c1, e2) in the sentence; ids go in "evidence" only.
5. "evidence" lists the ids of the facts you relied on. It must not be empty.
6. "question" is null unless the decision is "escalate". Write it to the person, as "you", in one
   sentence that names the weekday and time of the slot and the thing you could not settle. Phrase it so
   that YES means take the slot and NO means pass. Example of the shape: "Monday 9:30 AM is open but it
   is before 10; is your knee follow-up urgent enough to take it?"

Reply with JSON only:
{"decision": "accept" | "pass" | "escalate", "reason": "...", "evidence": ["c1", "e1"], "question": null}"""


@dataclass
class CalendarEvent:
    title: str
    start: str  # ISO local, e.g. 2026-10-12T14:30
    end: str


@dataclass
class Person:
    id: str
    name: str
    visit_reason: str
    constraints: list[str]
    calendar: list[CalendarEvent] = field(default_factory=list)
    current_appointment: str | None = None  # the appointment they hold today, ISO local

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "visit_reason": self.visit_reason,
            "constraints": self.constraints,
            "calendar": [vars(e) for e in self.calendar],
            "current_appointment": self.current_appointment,
        }


@dataclass
class Decision:
    decision: str
    reason: str
    evidence: list[str]
    question: str | None
    source: str  # model | code | guard | human
    model: str | None = None
    ms: int | None = None
    detail: str | None = None  # for the log, not for the person

    def public(self) -> dict[str, Any]:
        return vars(self).copy()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def calendar_facts(person: Person, slot_start: str, slot_minutes: int) -> tuple[dict[str, str], list[CalendarEvent]]:
    """Plain code. Returns ({fact_id: sentence}, overlapping_events)."""
    start = _dt(slot_start)
    end = start + timedelta(minutes=slot_minutes)
    facts: dict[str, str] = {}
    overlaps: list[CalendarEvent] = []
    same_day = sorted(
        (e for e in person.calendar if _dt(e.start).date() == start.date()),
        key=lambda e: e.start,
    )
    n = 0

    def add(text: str) -> None:
        nonlocal n
        n += 1
        facts[f"e{n}"] = text

    add(
        f"The offered slot is {start.strftime('%A %Y-%m-%d')}, {_clock(start)} to {_clock(end)}. "
        f"It starts {'before' if start.hour < 12 else 'after'} noon."
    )
    for event in same_day:
        if _dt(event.start) < end and _dt(event.end) > start:
            overlaps.append(event)
            add(f"OVERLAP: '{event.title}' runs {_clock(_dt(event.start))} to {_clock(_dt(event.end))}, which overlaps the slot.")
    before = [e for e in same_day if _dt(e.end) <= start]
    after = [e for e in same_day if _dt(e.start) >= end]
    if before:
        last = before[-1]
        gap = int((start - _dt(last.end)).total_seconds() // 60)
        add(f"Before the slot: '{last.title}' ends {_clock(_dt(last.end))}, leaving {gap} free minutes before the slot starts.")
    else:
        add("Before the slot: nothing else is on the calendar earlier that day.")
    if after:
        first = after[0]
        gap = int((_dt(first.start) - end).total_seconds() // 60)
        add(f"After the slot: '{first.title}' starts {_clock(_dt(first.start))}, {gap} minutes after the slot ends.")
    else:
        add("After the slot: nothing else is on the calendar later that day.")
    return facts, overlaps


def build_facts(person: Person, slot: dict[str, Any]) -> tuple[dict[str, str], list[CalendarEvent]]:
    facts = {f"c{i}": text for i, text in enumerate(person.constraints, start=1)}
    cal, overlaps = calendar_facts(person, slot["start"], int(slot["minutes"]))
    facts.update(cal)
    facts["t1"] = f"Reason for visit, as written on the ticket: {person.visit_reason}"
    if person.current_appointment:
        facts["t2"] = f"They already hold an appointment on {person.current_appointment.replace('T', ' at ')}; this slot would replace it."
    return facts, overlaps


def default_question(slot: dict[str, Any]) -> str:
    start = _dt(slot["start"])
    return f"{slot.get('where', 'The front desk')} has an opening {start.strftime('%A')} at {_clock(start)}. Take it?"


def guard(raw: dict[str, Any], facts: dict[str, str], overlaps: list[CalendarEvent], slot: dict[str, Any]) -> Decision:
    """Check the model's answer against what code knows. Returns the decision that stands."""
    decision = str(raw.get("decision", "")).strip().lower()
    reason = " ".join(str(raw.get("reason", "")).split())
    evidence = [str(e) for e in raw.get("evidence") or [] if isinstance(e, (str, int))]
    question = raw.get("question")
    question = " ".join(str(question).split()) if question else None

    if decision not in DECISIONS or not reason:
        return Decision("escalate", "The model's answer was not usable, so I am asking you.", [], default_question(slot), "guard")
    unknown = [e for e in evidence if e not in facts]
    if not evidence or unknown:
        return Decision(
            "escalate",
            "The model could not point to a written fact for its answer, so I am asking you.",
            [e for e in evidence if e in facts],
            default_question(slot),
            "guard",
        )
    if decision == "accept" and overlaps:
        first = overlaps[0]
        return Decision(
            "escalate",
            f"Your calendar shows '{first.title}' at that time, and I will not move it without asking.",
            evidence,
            f"Your calendar shows '{first.title}' then. Take the {_clock(_dt(slot['start']))} slot anyway?",
            "guard",
        )
    if decision == "escalate" and not question:
        question = default_question(slot)
    if decision != "escalate":
        question = None
    return Decision(decision, reason, evidence, question, "model")


def decide_without_model(overlaps: list[CalendarEvent], slot: dict[str, Any], why: str) -> Decision:
    """The model did not answer. Never accept here."""
    if overlaps:
        first = overlaps[0]
        return Decision(
            "pass",
            f"Calendar shows '{first.title}' {_clock(_dt(first.start))} to {_clock(_dt(first.end))}, which overlaps. (Decided by code; the model did not answer.)",
            [],
            None,
            "code",
        )
    decision = Decision("escalate", "The model did not answer, so I am asking you instead of guessing.", [], default_question(slot), "code")
    decision.detail = why
    return decision


Notifier = Callable[[Person, str, dict[str, Any]], Awaitable[dict[str, Any]]]


class LineSitter:
    def __init__(
        self,
        person: Person,
        base_url: str,
        transcript: Transcript,
        client_factory: Callable[[], httpx.AsyncClient],
        notifier: Notifier | None = None,
        retry_confirm_for_demo: bool = False,
    ) -> None:
        self.person = person
        self.base_url = base_url.rstrip("/")
        self.transcript = transcript
        self._client = client_factory
        self._notifier = notifier
        self.retry_confirm_for_demo = retry_confirm_for_demo
        self.ticket: int | None = None
        self._ticket_secret: str | None = None
        self.desk_url: str | None = None
        self._pending: dict[str, asyncio.Future[bool]] = {}  # offer_id -> human answer
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def actor(self) -> str:
        return f"sitter:{self.person.id}"

    def agent_card(self) -> dict[str, Any]:
        return {
            "name": f"Line Sitter for {self.person.name}",
            "description": "Holds one person's place in a line and answers offers on their behalf.",
            "url": self.base_url,
            "version": "0.1.0",
            "protocolVersion": "take-a-number/0.1",
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["application/json"],
            "defaultOutputModes": ["application/json"],
            "skills": [
                {
                    "id": "offer",
                    "name": "Receive an offer",
                    "description": "POST /a2a/offer with a slot and a lease. The sitter answers the desk with accept or pass before the lease runs out.",
                    "tags": ["take-a-number", "line", "waitlist"],
                }
            ],
        }

    # ---- talking to the desk -------------------------------------------

    async def take_ticket(self, desk_url: str) -> int:
        self.desk_url = desk_url.rstrip("/")
        async with self._client() as client:
            r = await client.post(
                f"{self.desk_url}/a2a/take_ticket",
                json={"agent_url": self.base_url, "label": self.person.name},
            )
        r.raise_for_status()
        body = r.json()
        self.ticket = body["ticket"]
        self._ticket_secret = body["ticket_secret"]
        return body["ticket"]

    async def _desk(self, verb: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        body = {"ticket": self.ticket, "ticket_secret": self._ticket_secret, **payload}
        async with self._client() as client:
            r = await client.post(f"{self.desk_url}/a2a/{verb}", json=body)
        try:
            data = r.json()
        except ValueError:
            data = {"error": "bad_reply", "message": f"http {r.status_code}"}
        return r.status_code, data

    # ---- deciding --------------------------------------------------------

    async def decide(self, slot: dict[str, Any]) -> Decision:
        facts, overlaps = build_facts(self.person, slot)
        user = "Facts:\n" + "\n".join(f"{k}: {v}" for k, v in facts.items())
        try:
            result = await llm.complete_json(SYSTEM_PROMPT, user)
        except llm.LLMUnavailable as exc:
            return decide_without_model(overlaps, slot, str(exc)[:200] or "no reply")
        decision = guard(result.data, facts, overlaps, slot)
        decision.model = result.model
        decision.ms = result.ms
        return decision

    def on_offer(self, offer: dict[str, Any]) -> None:
        """Called by the HTTP route. Answers the desk in the background."""
        task = asyncio.create_task(self._handle_offer(offer))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            self.transcript.emit("agent_error", self.actor, message=f"{type(exc).__name__}: {str(exc)[:160]}")

    async def _handle_offer(self, offer: dict[str, Any]) -> None:
        if offer.get("ticket") != self.ticket:
            self.transcript.emit("agent_error", self.actor, message="offer was addressed to a ticket I do not hold; ignored")
            return
        slot, offer_id = offer["slot"], offer["offer_id"]
        self.desk_url = (offer.get("reply_to") or self.desk_url or "").rstrip("/")
        self.transcript.emit("deciding", self.actor, ticket=self.ticket, offer_id=offer_id)
        decision = await self.decide(slot)
        self.transcript.emit("decision", self.actor, ticket=self.ticket, offer_id=offer_id, **decision.public())

        if decision.decision == "escalate":
            answer = await self._ask_person(offer, decision.question or default_question(slot))
            if answer is None:
                self.transcript.emit("escalation_unanswered", self.actor, ticket=self.ticket, offer_id=offer_id)
                return  # the lease runs out at the desk; the slot moves on
            decision = Decision(
                "accept" if answer else "pass",
                f"{self.person.name} answered {'yes' if answer else 'no'}.",
                [],
                None,
                "human",
            )
            self.transcript.emit("decision", self.actor, ticket=self.ticket, offer_id=offer_id, **decision.public())

        if decision.decision == "pass":
            code = "schedule_conflict" if decision.source != "human" else "declined"
            status, body = await self._desk("pass", {"offer_id": offer_id, "reason_code": code})
            self.transcript.emit("wire", self.actor, to="desk", verb="pass", ticket=self.ticket,
                                 request={"offer_id": offer_id, "reason_code": code}, status=status, response=body)
            return

        status, body = await self._desk("accept", {"offer_id": offer_id})
        self.transcript.emit("wire", self.actor, to="desk", verb="accept", ticket=self.ticket,
                             request={"offer_id": offer_id}, status=status, response=body)
        if status != 200:
            self.transcript.emit("too_late", self.actor, ticket=self.ticket, offer_id=offer_id, error=body.get("error"))
            return
        key = f"{self.person.id}-{offer_id}-ticket-{self.ticket}"
        attempts = 2 if self.retry_confirm_for_demo else 1
        for attempt in range(1, attempts + 1):
            status, body = await self._desk("confirm", {"offer_id": offer_id, "idempotency_key": key})
            self.transcript.emit(
                "wire", self.actor, to="desk", verb="confirm", ticket=self.ticket,
                request={"offer_id": offer_id, "idempotency_key": key}, status=status, response=body,
                attempt=attempt, retry_on_purpose=attempt > 1,
            )
            if status != 200:
                self.transcript.emit("too_late", self.actor, ticket=self.ticket, offer_id=offer_id, error=body.get("error"))
                return
            if attempt < attempts:
                await asyncio.sleep(0.4)

    # ---- asking the person -----------------------------------------------

    async def _ask_person(self, offer: dict[str, Any], question: str) -> bool | None:
        offer_id = offer["offer_id"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[offer_id] = future
        delivery: dict[str, Any] = {"channel": "page", "simulated": False}
        if self._notifier is not None:
            try:
                delivery = await self._notifier(self.person, question, offer)
            except Exception as exc:
                delivery = {"channel": "page", "simulated": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        self.transcript.emit(
            "escalation", self.actor, ticket=self.ticket, offer_id=offer_id,
            question=question, lease_seconds=offer.get("lease_seconds"), delivery=delivery,
        )
        try:
            return await asyncio.wait_for(future, timeout=float(offer.get("lease_seconds") or 60))
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(offer_id, None)

    def pending_questions(self) -> list[str]:
        return list(self._pending)

    def human_answer(self, answer: bool, offer_id: str | None = None) -> bool:
        """The person said yes or no. Returns False if nothing was waiting."""
        if offer_id is None and self._pending:
            offer_id = next(iter(self._pending))
        future = self._pending.get(offer_id or "")
        if future is None or future.done():
            return False
        future.set_result(answer)
        self.transcript.emit("human_answer", self.actor, ticket=self.ticket, offer_id=offer_id, answer="yes" if answer else "no")
        return True

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
