"""The two agents talking over HTTP (in-process transport), with the model faked.

The model is faked so these run offline and the same way every time. What
is under test is everything around the model: the wire protocol, the
guards, and what happens when the model or a person does not answer.
"""

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest

from integrations import darwin, dial, keewano
from take_a_number import app as app_module
from take_a_number import llm
from take_a_number.app import World, app, state
from take_a_number.line_sitter import CalendarEvent, build_facts, guard
from take_a_number.scenario import people, today

MARIA = "hi this is Maria I can't make my 3pm tomorrow"
TOM = "This is Tom. Something came up, please cancel my 9:30 tomorrow morning."


def fake_model(decide_fails=False, parse_fails=False, decide_reply=None):
    async def complete_json(system, user, timeout_s=8.0):
        if system.startswith("You read messages"):
            if parse_fails:
                raise llm.LLMUnavailable("gemini: timed out after 8s")
            tomorrow = people(today())[0].calendar[0].start[:10]
            if "Maria" in user:
                data = {"intent": "cancel", "patient_name": "Maria", "date": tomorrow, "time": "15:00"}
            elif "Tom" in user:
                data = {"intent": "cancel", "patient_name": "Tom", "date": tomorrow, "time": "09:30"}
            elif "cancel" in user.lower():
                data = {"intent": "cancel", "patient_name": None, "date": None, "time": "15:00"}
            else:
                data = {"intent": "other", "patient_name": None, "date": None, "time": None}
            return llm.LLMResult(data, "fake", "fake-model", 1)
        if decide_fails:
            raise llm.LLMUnavailable("gemini: timed out after 8s")
        if decide_reply is not None:
            return llm.LLMResult(decide_reply, "fake", "fake-model", 1)
        if "OVERLAP" in user:
            data = {"decision": "pass", "reason": "The quarterly review overlaps and you are presenting.", "evidence": ["c1", "e2"], "question": None}
        elif "9:30 AM" in user and "unless it is urgent" in user:
            data = {"decision": "escalate", "reason": "The slot is before 10 and nothing says whether this is urgent.", "evidence": ["c2", "t1"], "question": "Is this visit urgent enough for 9:30 AM?"}
        else:
            data = {"decision": "accept", "reason": "Nothing on the calendar conflicts.", "evidence": ["e1"], "question": None}
        return llm.LLMResult(data, "fake", "fake-model", 1)

    return complete_json


@pytest.fixture
async def make_world(monkeypatch, tmp_path):
    transport = httpx.ASGITransport(app=app)
    factory = lambda: httpx.AsyncClient(transport=transport, base_url="http://test", timeout=5.0)  # noqa: E731
    monkeypatch.setattr(llm, "FORCE_UNAVAILABLE", False)
    for name in ("DIAL_API_KEY", "DIAL_FROM_NUMBER", "DEMO_TO_NUMBER", "DIAL_WEBHOOK_SECRET", "DARWIN_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    made = []

    async def make(lease_seconds=60.0, model=None, take_tickets=True):
        monkeypatch.setattr(llm, "complete_json", model or fake_model())
        world = World("http://test", factory, lease_seconds=lease_seconds, log_dir=str(tmp_path))
        state.world = world
        if take_tickets:
            await world.open()
        made.append(world)
        return world, factory

    yield make
    for world in made:
        await world.close()
    state.world = None


async def until(world, type_, count=1, timeout=3.0, sweep=False):
    """Wait until `count` events of a type exist."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if sweep:
            await world.desk.sweep()
        found = [e for e in world.transcript.all() if e["type"] == type_]
        if len(found) >= count:
            return found
        await asyncio.sleep(0.01)
    raise AssertionError(f"never saw {count} x {type_}; saw {[e['type'] for e in world.transcript.all()]}")


def types(world):
    return [e["type"] for e in world.transcript.all()]


# ---- the demo flow -------------------------------------------------------


async def test_cancellation_41_passes_42_books_and_the_retry_is_harmless(make_world):
    world, _ = await make_world()
    assert world.desk.line.waiting() == [41, 42, 43]

    await world.desk.handle_cancellation(MARIA)
    confirmed = await until(world, "confirmed", count=2)

    assert [e["ticket"] for e in world.transcript.all() if e["type"] == "offer"] == [41, 42]
    assert confirmed[0]["replayed"] is False and confirmed[1]["replayed"] is True
    assert confirmed[0]["booking_id"] == confirmed[1]["booking_id"]
    assert confirmed[1]["bookings_for_slot"] == 1  # the retry did not double-book
    assert len(world.desk.line.snapshot()["bookings"]) == 1
    assert world.desk.line.waiting() == [41, 43]  # 41 passed and is still first
    assert world.desk.line.position(41) == 1
    booked = next(a for a in world.desk.schedule if a["start"].endswith("T15:00"))
    assert booked["patient"] == "Ana"


async def test_ticket_secret_and_calendar_never_reach_the_transcript_wire(make_world):
    world, _ = await make_world()
    await world.desk.handle_cancellation(MARIA)
    await until(world, "confirmed", count=2)
    text = json.dumps(world.transcript.all())
    for secret in world.desk._secrets.values():
        assert secret not in text
    passed = next(e for e in world.transcript.all() if e["type"] == "wire" and e["verb"] == "pass")
    assert "Quarterly" not in json.dumps(passed)  # the desk is told a code, not what is on the calendar
    assert passed["request"]["reason_code"] == "schedule_conflict"


async def test_agent_cards_are_published(make_world):
    _, factory = await make_world()
    async with factory() as client:
        for path in ("/.well-known/agent.json", "/.well-known/agent-card.json", "/sitters/ana/.well-known/agent-card.json"):
            r = await client.get(path)
            assert r.status_code == 200 and r.json()["protocolVersion"] == "take-a-number/0.1"
        desk = (await client.get("/.well-known/agent-card.json")).json()
    assert {s["id"] for s in desk["skills"]} == {"take_ticket", "position", "accept", "pass", "confirm"}


async def test_wrong_ticket_secret_is_refused(make_world):
    world, factory = await make_world(model=fake_model(decide_reply={"decision": "escalate", "reason": "x", "evidence": ["e1"], "question": "Take it?"}))
    await world.desk.handle_cancellation(MARIA)
    await until(world, "escalation")
    offer_id = next(e["offer_id"] for e in world.transcript.all() if e["type"] == "offer")
    async with factory() as client:
        r = await client.post("/desk/a2a/accept", json={"ticket": 41, "ticket_secret": "guess", "offer_id": offer_id})
        assert r.status_code == 403
        r = await client.post("/desk/a2a/accept", json={"ticket": 42, "ticket_secret": world.desk._secrets[42], "offer_id": offer_id})
        assert r.status_code == 403 and r.json()["error"] == "not_your_offer"


async def test_forged_offer_naming_another_desk_is_ignored_and_leaks_nothing(make_world):
    world, factory = await make_world()
    async with factory() as client:
        r = await client.post("/sitters/dev/a2a/offer", json={
            "offer_id": "offer-evil", "ticket": 41, "lease_seconds": 60, "reply_to": "http://evil.example/desk",
            "slot": {"slot_id": "x", "start": "2026-10-12T15:00", "minutes": 30}})
    assert r.status_code == 200
    errors = await until(world, "agent_error")
    assert "ignored" in errors[0]["message"]
    assert "deciding" not in types(world) and world.sitters["dev"].desk_url == "http://test/desk"


# ---- failure paths -------------------------------------------------------


async def test_model_timeout_on_the_cancellation_goes_to_staff_and_frees_nothing(make_world):
    world, _ = await make_world(model=fake_model(parse_fails=True))
    out = await world.desk.handle_cancellation(MARIA)
    assert out["freed"] is None and "needs_staff" in types(world)
    assert "slot_freed" not in types(world) and "offer" not in types(world)
    assert all(a["status"] == "booked" for a in world.desk.schedule)


async def test_vague_or_unrelated_messages_free_nothing(make_world):
    world, _ = await make_world()
    assert (await world.desk.handle_cancellation("please cancel my appointment at 3"))["freed"] is None  # only a time
    assert (await world.desk.handle_cancellation("what are your opening hours?"))["freed"] is None
    assert (await world.desk.handle_cancellation("   "))["freed"] is None
    assert types(world).count("needs_staff") == 3 and "slot_freed" not in types(world)


async def test_model_timeout_on_the_decision_never_accepts_by_itself(make_world):
    world, _ = await make_world(model=fake_model(decide_fails=True))
    await world.desk.handle_cancellation(MARIA)
    escalation = (await until(world, "escalation"))[0]
    decisions = [e for e in world.transcript.all() if e["type"] == "decision"]
    assert (decisions[0]["ticket"], decisions[0]["decision"], decisions[0]["source"]) == (41, "pass", "code")  # overlap seen by code
    assert (decisions[1]["ticket"], decisions[1]["decision"], decisions[1]["source"]) == (42, "escalate", "code")
    assert escalation["delivery"]["simulated"] is True  # no Dial key in tests, and it says so
    assert "confirmed" not in types(world)
    assert world.sitters["ana"].human_answer(True)
    confirmed = await until(world, "confirmed")
    assert confirmed[0]["ticket"] == 42


async def test_outage_switch_makes_sitters_decide_without_the_model(make_world, monkeypatch):
    world, _ = await make_world()
    monkeypatch.setattr(llm, "FORCE_UNAVAILABLE", True)
    await world.desk.handle_cancellation(MARIA)  # the desk still reads the message
    await until(world, "escalation")
    decisions = [(e["ticket"], e["decision"], e["source"]) for e in world.transcript.all() if e["type"] == "decision"]
    assert decisions == [(41, "pass", "code"), (42, "escalate", "code")]
    assert "confirmed" not in types(world)


async def test_unanswered_question_lets_the_lease_expire_and_the_slot_moves_on(make_world):
    world, _ = await make_world(lease_seconds=0.15)
    await world.desk.handle_cancellation(TOM)  # 9:30: ticket 41 must ask its person
    await until(world, "escalation")
    expired = await until(world, "lease_expired", sweep=True)
    assert expired[0]["ticket"] == 41 and expired[0]["misses"] == 1 and expired[0]["position"] == 1
    offers = await until(world, "offer", count=2, sweep=True)
    assert offers[1]["ticket"] == 42
    assert world.desk.line.ticket(41).status == "waiting"  # one miss keeps the place


async def test_lease_expiring_between_decision_and_accept_does_not_book(make_world):
    world, factory = await make_world(lease_seconds=0.1, model=fake_model(decide_reply={"decision": "escalate", "reason": "x", "evidence": ["e1"], "question": "Take it?"}))
    await world.desk.handle_cancellation(MARIA)
    await until(world, "escalation")
    offer_id = next(e["offer_id"] for e in world.transcript.all() if e["type"] == "offer")
    await asyncio.sleep(0.15)  # the lease runs out; no sweep has happened yet
    async with factory() as client:
        r = await client.post("/desk/a2a/accept", json={"ticket": 41, "ticket_secret": world.desk._secrets[41], "offer_id": offer_id})
    assert r.status_code == 410 and r.json()["error"] == "lease_expired"
    expired = await until(world, "lease_expired")
    assert expired[0]["noticed_by"] == "late answer"
    assert world.desk.line.slot(offer_id and expired[0]["slot_id"]).booked_by is None
    assert (await until(world, "offer", count=2))[1]["ticket"] == 42  # and the slot moved on


async def test_answer_arriving_after_the_sweeper_expired_the_lease_is_refused_and_reported_once(make_world):
    world, _ = await make_world(lease_seconds=0.1)
    await world.desk.handle_cancellation(TOM)
    await until(world, "escalation")
    await until(world, "lease_expired", sweep=True)  # the sweeper notices first
    late = world.sitters["dev"]
    status, body = await late._desk("accept", {"offer_id": "offer-1"})  # the person said yes too late
    assert status == 410 and body["error"] == "lease_expired"
    await asyncio.sleep(0.05)
    expired_for_41 = [e for e in world.transcript.all() if e["type"] == "lease_expired" and e["ticket"] == 41]
    assert len(expired_for_41) == 1 and world.desk.line.ticket(41).misses == 1  # said once, counted once


async def test_duplicate_confirm_over_http_returns_the_same_booking(make_world):
    world, factory = await make_world()
    await world.desk.handle_cancellation(MARIA)
    first = (await until(world, "confirmed", count=2))[0]
    wire = next(e for e in world.transcript.all() if e["type"] == "wire" and e["verb"] == "confirm")
    body = {"ticket": 42, "ticket_secret": world.desk._secrets[42], **wire["request"]}
    async with factory() as client:
        replies = [(await client.post("/desk/a2a/confirm", json=body)).json() for _ in range(3)]
        other_key = await client.post("/desk/a2a/confirm", json=body | {"idempotency_key": "a-different-key"})
    assert {r["booking_id"] for r in replies} == {first["booking_id"]} and all(r["replayed"] for r in replies)
    assert other_key.status_code == 409 and other_key.json()["error"] == "idempotency_conflict"
    assert len(world.desk.line.snapshot()["bookings"]) == 1


async def test_empty_line_reports_the_slot_unfilled(make_world):
    world, _ = await make_world(take_tickets=False)
    await world.desk.handle_cancellation(MARIA)
    assert types(world)[-2:] == ["slot_freed", "slot_unfilled"]


async def test_everyone_passes_then_slot_is_unfilled_and_all_keep_their_place(make_world):
    world, _ = await make_world(model=fake_model(decide_reply={"decision": "pass", "reason": "Not today.", "evidence": ["e1"], "question": None}))
    await world.desk.handle_cancellation(MARIA)
    await until(world, "slot_unfilled")
    assert [e["ticket"] for e in world.transcript.all() if e["type"] == "offer"] == [41, 42, 43]
    assert world.desk.line.waiting() == [41, 42, 43]


async def test_unreachable_sitter_counts_as_a_miss_and_the_slot_moves_on(make_world):
    world, _ = await make_world(take_tickets=False)
    world.desk.take_ticket("http://test/sitters/nobody", "Ghost")  # 404s on offer
    for sitter in world.sitters.values():
        await sitter.take_ticket(world.desk.base_url)
    await world.desk.handle_cancellation(MARIA)
    await until(world, "confirmed")
    undeliverable = [e for e in world.transcript.all() if e["type"] == "offer_undeliverable"]
    assert undeliverable[0]["ticket"] == 41 and undeliverable[0]["misses"] == 1


async def test_two_cancellations_at_once_strand_no_slot(make_world):
    world, _ = await make_world()
    await asyncio.gather(world.desk.handle_cancellation(MARIA), world.desk.handle_cancellation(TOM))
    for _ in range(300):  # sweep like the server does, and answer any question with no
        await world.desk.sweep()
        for sitter in world.sitters.values():
            if sitter.pending_questions():
                sitter.human_answer(False)
        slots = {s["slot_id"]: s["status"] for s in world.desk.line.snapshot()["slots"]}
        if len(slots) == 2 and all(status in ("booked", "unfilled") for status in slots.values()):
            break
        await asyncio.sleep(0.01)
    assert len(slots) == 2 and all(status in ("booked", "unfilled") for status in slots.values()), slots
    booked = [b["ticket"] for b in world.desk.line.snapshot()["bookings"]]
    assert len(booked) == len(set(booked))  # nobody got two slots
    assert "agent_error" not in types(world)


# ---- the guard: the model cannot invent or overrule ------------------------


def _dev_facts():
    dev = people(today())[0]
    slot = {"start": dev.calendar[0].start[:10] + "T15:00", "minutes": 30, "where": "Corner Clinic"}
    facts, overlaps = build_facts(dev, slot)
    return facts, overlaps, slot


def test_guard_turns_accept_over_a_calendar_overlap_into_a_question():
    facts, overlaps, slot = _dev_facts()
    assert overlaps and isinstance(overlaps[0], CalendarEvent)
    d = guard({"decision": "accept", "reason": "Looks fine.", "evidence": ["e1"]}, facts, overlaps, slot)
    assert d.decision == "escalate" and d.source == "guard" and "Quarterly review" in d.question


def test_guard_rejects_answers_that_cite_nothing_or_cite_facts_that_do_not_exist():
    facts, overlaps, slot = _dev_facts()
    for raw in (
        {"decision": "pass", "reason": "He is travelling that day.", "evidence": []},
        {"decision": "pass", "reason": "He is travelling that day.", "evidence": ["c9"]},
        {"decision": "maybe", "reason": "Hmm.", "evidence": ["c1"]},
        {"decision": "pass", "reason": "", "evidence": ["c1"]},
        {},
    ):
        d = guard(raw, facts, overlaps, slot)
        assert d.decision == "escalate" and d.source == "guard" and d.question


def test_guard_passes_a_grounded_answer_through():
    facts, overlaps, slot = _dev_facts()
    d = guard({"decision": "pass", "reason": "You are presenting then.", "evidence": ["c1", "e2"], "question": "ignored"}, facts, overlaps, slot)
    assert (d.decision, d.source, d.question) == ("pass", "model", None)


# ---- integrations ----------------------------------------------------------


async def test_dial_is_simulated_and_says_so_without_all_three_variables(monkeypatch):
    for name in dial.NEEDED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DIAL_API_KEY", "set-but-alone")
    out = await dial.send_text("hello")
    assert out["simulated"] is True and "no text was sent" in out["note"]
    assert dial.status()["missing"] == ["DIAL_FROM_NUMBER", "DEMO_TO_NUMBER"]


def test_dial_signature_check():
    body, secret, t = b'{"type":"message.received"}', "whsec_test", 1_700_000_000
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert dial.verify_signature(body, f"t={t},v1={sig}", secret, now=t + 10)
    assert not dial.verify_signature(body, f"t={t},v1={sig}", secret, now=t + 301)  # too old
    assert not dial.verify_signature(body + b" ", f"t={t},v1={sig}", secret, now=t)  # body changed
    assert not dial.verify_signature(body, "garbage", secret, now=t)


def test_dial_inbound_parsing():
    event = {"type": "message.received", "data": {"messageId": "msg_1", "from": "+14155550123", "body": " Y "}}
    assert dial.parse_inbound(event) == {"from": "+14155550123", "text": "Y", "id": "msg_1"}
    assert dial.parse_inbound({"type": "call.ended"}) is None
    assert (dial.yes_no("Y"), dial.yes_no("no."), dial.yes_no("cancel my 3pm")) == (True, False, None)


async def test_webhook_refuses_unverifiable_deliveries(make_world):
    _, factory = await make_world()
    async with factory() as client:
        r = await client.post("/integrations/dial/webhook", json={"type": "message.received"})
    assert r.status_code == 503  # no secret set: off, loudly, not silently accepted


async def test_a_simulated_text_can_cancel_and_simulated_replies_can_answer(make_world):
    world, factory = await make_world()
    async with factory() as client:
        await client.post("/demo/text", json={"text": TOM})  # 9:30 AM: both Dev and Ana have a "before 10" rule
        await until(world, "escalation")
        r = await client.post("/demo/text", json={"text": "N", "from_person": "dev"})
        assert r.json() == {"answered": True}
        await until(world, "escalation", count=2)
        r = await client.post("/demo/text", json={"text": "no", "from_person": "ana"})
        assert r.json() == {"answered": True}
    confirmed = await until(world, "confirmed")
    assert confirmed[0]["ticket"] == 43  # Sam has no morning rule
    texts = [e for e in world.transcript.all() if e["type"] == "text_in"]
    assert all(e["simulated"] for e in texts)
    assert [e["routed_to"] for e in texts] == ["desk", "sitter:dev", "sitter:ana"]
    assert world.desk.line.waiting() == [41, 42]  # both said no and both kept their place


async def test_event_log_answers_how_long_and_why(make_world):
    world, _ = await make_world()
    await world.desk.handle_cancellation(MARIA)
    await until(world, "confirmed", count=2)
    answer = keewano.how_long_and_why(world.log)
    assert answer["events_read"] == len(world.transcript.all())
    assert answer["served"]["ticket"] == 42 and answer["served"]["wait_s"] >= 0
    assert answer["passed"]["ticket"] == 41 and "presenting" in answer["passed"]["answer"]
    assert answer["simulated_keewano"] is True and keewano.backend(world.log)["simulated"] is True


def test_darwin_listing_is_built_from_the_agent_card_and_never_submitted():
    card = {"name": "Corner Clinic front desk", "description": "Runs the line.", "protocolVersion": "take-a-number/0.1",
            "skills": [{"id": "take_ticket", "name": "Take a ticket", "description": "Join."}], "takeANumber": {"lease_seconds": 60}}
    out = darwin.listing(card, "https://example.org/")
    assert out["ai"]["links"] == ["https://example.org/.well-known/agent-card.json"]
    assert out["listing"]["status"] == "DRAFT" and out["listing"]["type"] == "CAPABILITY"
    assert darwin.status()["listed"] is False and len(darwin.commands()) == 2
