"""The Front Desk: one agent per service (a clinic, a DMV office, a restaurant).

It owns a schedule and a line. It reads a cancellation written in plain
language, frees the slot, and offers it down the line one ticket at a time.

The model is used for exactly one thing here: turning the cancellation text
into {name, date, time}. Matching that against the schedule is plain code,
and it must match exactly one appointment or the message goes to staff.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import date, datetime
from typing import Any, Callable

import httpx

from . import llm
from .events import Transcript
from .queue import LineError, TicketLine

PARSE_PROMPT = """You read messages sent to a front desk. Decide whether the message cancels an appointment.

Reply with JSON only:
{"intent": "cancel" | "other", "patient_name": string or null, "date": "YYYY-MM-DD" or null, "time": "HH:MM" (24 hour) or null}

Rules:
- Use only what the message says. If a part is not stated, use null. Do not guess.
- Resolve words like "today" and "tomorrow" against the current date given below.
- "3pm" is "15:00". "9:30" with "morning" is "09:30"."""


class FrontDesk:
    def __init__(
        self,
        name: str,
        base_url: str,
        schedule: list[dict[str, Any]],
        transcript: Transcript,
        client_factory: Callable[[], httpx.AsyncClient],
        today: date,
        lease_seconds: float = 60.0,
        first_ticket: int = 41,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.schedule = schedule
        self.transcript = transcript
        self._client = client_factory
        self.today = today
        self.lease_seconds = lease_seconds
        self.line = TicketLine(first_number=first_ticket, **({"clock": clock} if clock else {}))
        self._secrets: dict[int, str] = {}
        self._labels: dict[int, str] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._advance_lock = asyncio.Lock()
        self._expired_reported: set[str] = set()

    actor = "desk"

    def agent_card(self) -> dict[str, Any]:
        return {
            "name": f"{self.name} front desk",
            "description": "Runs the line for this service. Take a ticket, and the desk offers you freed appointment slots in ticket order, each under a short lease.",
            "url": self.base_url,
            "version": "0.1.0",
            "protocolVersion": "take-a-number/0.1",
            "capabilities": {"streaming": False, "pushNotifications": True},
            "defaultInputModes": ["application/json"],
            "defaultOutputModes": ["application/json"],
            "skills": [
                {"id": "take_ticket", "name": "Take a ticket", "description": "POST /a2a/take_ticket {agent_url}. Returns ticket number, position, and a ticket secret. One ticket per agent.", "tags": ["line", "waitlist", "queue"]},
                {"id": "position", "name": "Position", "description": "GET /a2a/position/{ticket}. Returns how many tickets are ahead.", "tags": ["line"]},
                {"id": "accept", "name": "Accept an offer", "description": "POST /a2a/accept {ticket, ticket_secret, offer_id}. Holds the slot until the lease runs out.", "tags": ["offer", "lease"]},
                {"id": "pass", "name": "Pass on an offer", "description": "POST /a2a/pass {ticket, ticket_secret, offer_id}. The slot goes to the next ticket. You keep your place.", "tags": ["offer"]},
                {"id": "confirm", "name": "Confirm", "description": "POST /a2a/confirm {ticket, ticket_secret, offer_id, idempotency_key}. Books the held slot. A retry with the same key returns the same booking.", "tags": ["booking", "idempotent"]},
            ],
            "takeANumber": {"lease_seconds": self.lease_seconds, "max_misses": self.line.max_misses, "offers_are_pushed_to": "{agent_url}/a2a/offer"},
        }

    # ---- protocol verbs (called by the HTTP routes) ----------------------

    def take_ticket(self, agent_url: str, label: str = "") -> dict[str, Any]:
        ticket = self.line.take_ticket(agent_url.rstrip("/"))
        new = ticket.number not in self._secrets
        if new:
            self._secrets[ticket.number] = secrets.token_urlsafe(16)
            self._labels[ticket.number] = label
            self.transcript.emit(
                "ticket_taken", self.actor, ticket=ticket.number, label=label,
                position=self.line.position(ticket.number), agent_url=agent_url,
            )
        return {
            "ticket": ticket.number,
            "position": self.line.position(ticket.number),
            "ticket_secret": self._secrets[ticket.number],
            "lease_seconds": self.lease_seconds,
        }

    def position(self, ticket: int) -> dict[str, Any]:
        t = self.line.ticket(ticket)
        return {"ticket": ticket, "status": t.status, "position": self.line.position(ticket), "misses": t.misses}

    def check_secret(self, ticket: int, secret: str | None) -> bool:
        expected = self._secrets.get(ticket)
        return bool(expected) and secrets.compare_digest(expected, secret or "")

    def accept(self, ticket: int, offer_id: str) -> dict[str, Any]:
        offer = self.line.accept(offer_id, ticket)
        self.transcript.emit("held", self.actor, ticket=ticket, offer_id=offer_id, slot_id=offer.slot_id)
        return {"status": "held", "offer_id": offer_id, "slot_id": offer.slot_id}

    def pass_(self, ticket: int, offer_id: str, reason_code: str = "") -> dict[str, Any]:
        offer = self.line.pass_(offer_id, ticket)
        self.transcript.emit(
            "passed", self.actor, ticket=ticket, offer_id=offer_id, slot_id=offer.slot_id,
            reason_code=reason_code, position=self.line.position(ticket),
        )
        self._spawn(self.advance(offer.slot_id))
        return {"status": "passed", "position": self.line.position(ticket), "kept_place": True}

    def confirm(self, ticket: int, offer_id: str, idempotency_key: str) -> dict[str, Any]:
        booking, replayed = self.line.confirm(offer_id, ticket, idempotency_key)
        if not replayed:
            for appt in self.schedule:
                if appt["slot_id"] == booking.slot_id:
                    appt["patient"] = self._labels.get(ticket) or f"ticket {ticket}"
                    appt["status"] = "booked"
        self.transcript.emit(
            "confirmed", self.actor, ticket=ticket, offer_id=offer_id, slot_id=booking.slot_id,
            booking_id=booking.booking_id, replayed=replayed,
            bookings_for_slot=sum(1 for b in self.line.snapshot()["bookings"] if b["slot_id"] == booking.slot_id),
            waiting=self.line.waiting(),
        )
        return {"status": "booked", "booking_id": booking.booking_id, "slot_id": booking.slot_id, "replayed": replayed}

    # ---- cancellation ----------------------------------------------------

    async def handle_cancellation(self, text: str, channel: str = "web") -> dict[str, Any]:
        text = " ".join(text.split())[:500]
        self.transcript.emit("cancellation_received", self.actor, text=text, channel=channel)
        if not text:
            return self._needs_staff("the message was empty")
        user = f"Current date: {self.today.isoformat()} ({self.today.strftime('%A')}).\nMessage: {text}"
        try:
            result = await llm.complete_json(PARSE_PROMPT, user)
        except llm.LLMUnavailable as exc:
            return self._needs_staff(f"the model did not answer ({str(exc)[:80]}); the message is kept for staff")
        parsed = {k: result.data.get(k) for k in ("intent", "patient_name", "date", "time")}
        self.transcript.emit("cancellation_parsed", self.actor, parsed=parsed, model=result.model, ms=result.ms)
        if parsed["intent"] != "cancel":
            return self._needs_staff("the message does not read as a cancellation")
        matches, used = self._match(parsed)
        if used < 2:
            return self._needs_staff("the message does not say enough to pick one appointment (need two of: name, date, time)")
        if len(matches) != 1:
            return self._needs_staff(f"{len(matches)} appointments match; exactly one must")
        appt = matches[0]
        freed_from = appt["patient"]
        appt["patient"], appt["status"] = None, "free"
        slot_info = {k: appt[k] for k in ("slot_id", "start", "minutes", "with")} | {"where": self.name}
        self.line.open_slot(appt["slot_id"], slot_info)
        self.transcript.emit("slot_freed", self.actor, slot=slot_info, freed_from=freed_from)
        await self.advance(appt["slot_id"])
        return {"freed": slot_info}

    def _needs_staff(self, why: str) -> dict[str, Any]:
        self.transcript.emit("needs_staff", self.actor, why=why)
        return {"freed": None, "needs_staff": why}

    def _match(self, parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """Plain code. Every stated part must match; returns (matches, parts_used)."""
        name = (parsed.get("patient_name") or "").strip().lower()
        day, clock = parsed.get("date"), parsed.get("time")
        used = sum(1 for part in (name, day, clock) if part)
        out = []
        for appt in self.schedule:
            if appt["status"] != "booked" or not appt.get("patient"):
                continue
            start = datetime.fromisoformat(appt["start"])
            if day and start.date().isoformat() != day:
                continue
            if clock and start.strftime("%H:%M") != clock:
                continue
            if name:
                patient = appt["patient"].lower()
                first = name.split()[0]
                if first not in patient.replace(".", "").split():
                    continue
            out.append(appt)
        return out, used

    # ---- the offer loop --------------------------------------------------

    async def advance(self, slot_id: str) -> None:
        """Offer the slot to the next ticket. Loops only past agents it cannot reach."""
        async with self._advance_lock:
            while True:
                offer = self.line.next_offer(slot_id, self.lease_seconds)
                if offer is None:
                    slot = self.line.slot(slot_id)
                    if slot.status == "unfilled":
                        self.transcript.emit(
                            "slot_unfilled", self.actor, slot_id=slot_id, asked=list(slot.tried),
                            note="Nobody in line took it. The slot goes back to normal booking.",
                        )
                    return
                ticket = self.line.ticket(offer.ticket)
                payload = {
                    "verb": "offer",
                    "offer_id": offer.offer_id,
                    "ticket": offer.ticket,
                    "slot": self.line.slot(slot_id).info,
                    "lease_seconds": self.lease_seconds,
                    "reply_to": self.base_url,
                }
                self.transcript.emit(
                    "offer", self.actor, to=f"ticket {offer.ticket}", ticket=offer.ticket,
                    offer_id=offer.offer_id, slot=payload["slot"], lease_seconds=self.lease_seconds,
                )
                try:
                    async with self._client() as client:
                        r = await client.post(f"{ticket.holder}/a2a/offer", json=payload, timeout=5.0)
                    r.raise_for_status()
                    return  # delivered; the sitter answers, or the lease runs out
                except Exception as exc:
                    self.line.fail_delivery(offer.offer_id)
                    t = self.line.ticket(offer.ticket)
                    self.transcript.emit(
                        "offer_undeliverable", self.actor, ticket=offer.ticket, offer_id=offer.offer_id,
                        error=type(exc).__name__, misses=t.misses, ticket_status=t.status,
                    )

    def report_expired(self, offer_id: str, noticed_by: str = "sweeper") -> bool:
        """Say once, on the transcript, that a lease ran out. Returns False if already said."""
        if offer_id in self._expired_reported:
            return False
        self._expired_reported.add(offer_id)
        offer = self.line.offer(offer_id)
        t = self.line.ticket(offer.ticket)
        self.transcript.emit(
            "lease_expired", self.actor, ticket=offer.ticket, offer_id=offer.offer_id, slot_id=offer.slot_id,
            misses=t.misses, max_misses=self.line.max_misses, ticket_status=t.status,
            position=self.line.position(offer.ticket), noticed_by=noticed_by,
        )
        return True

    async def sweep(self) -> int:
        """Release leases that ran out and move those slots on. Returns how many expired."""
        expired = self.line.expire_due()
        for offer in expired:
            self.report_expired(offer.offer_id)
            await self.advance(offer.slot_id)
        # A slot can be left open with nobody asked, when everyone still eligible was busy
        # answering another slot. Try those again so no slot is stranded.
        for slot_id in self.line.open_slots():
            await self.advance(slot_id)
        return len(expired)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                exc = t.exception()
                self.transcript.emit("agent_error", self.actor, message=f"{type(exc).__name__}: {str(exc)[:160]}")

        task.add_done_callback(done)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()


def error_body(exc: LineError) -> dict[str, Any]:
    return {"error": exc.code, "message": str(exc)}
