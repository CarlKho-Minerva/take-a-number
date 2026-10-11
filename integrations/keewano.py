"""Questions answered from the complete, ordered event sequence.

Keewano's database is built for this kind of question: not "how many
confirms happened" but "what happened to ticket 42, in order, and why".

Status, stated plainly: KeewanoDB Local had no public download or install
doc on 2026-10-11 (the product page returned 404), so this build keeps the
log in JSONL (see eventlog.py) and answers the question with the plain
Python below. `backend()` reports which one is in use and the page prints it.
The cloud ingest API is documented, but it takes numeric event type ids
registered ahead of time and needs an account; it is not wired here.
"""

from __future__ import annotations

from typing import Any

from .eventlog import EventLog


def backend(log: EventLog) -> dict[str, Any]:
    return {
        "tool": "Keewano",
        "simulated": True,
        "backend": log.backend,
        "note": "JSONL event log behind the EventLog interface. KeewanoDB Local was not publicly installable on 2026-10-11.",
    }


def ticket_story(events: list[dict[str, Any]], ticket: int) -> list[dict[str, Any]]:
    """Every event that touched one ticket, in order."""
    return [e for e in events if e.get("ticket") == ticket]


def how_long_and_why(log: EventLog) -> dict[str, Any]:
    """How long did the booked ticket wait, and why did the one before it pass?

    Reads the most recent booking in the log and follows that slot's events
    backwards. Every number in the answer carries the seq numbers of the
    events it was read from.
    """
    events = log.events()
    out: dict[str, Any] = {"backend": log.backend, "simulated_keewano": True, "events_read": len(events)}
    confirmed = next((e for e in reversed(events) if e["type"] == "confirmed" and not e.get("replayed")), None)
    if confirmed is None:
        out["served"] = {"ticket": None, "answer": "The log shows no booking yet."}
        out["passed"] = {"ticket": None, "answer": "The log shows no pass yet."}
        return out

    served, slot_id = confirmed["ticket"], confirmed["slot_id"]
    taken = next((e for e in events if e["type"] == "ticket_taken" and e["ticket"] == served), None)
    offered = next((e for e in events if e["type"] == "offer" and e.get("offer_id") == confirmed["offer_id"]), None)
    freed = next((e for e in reversed(events) if e["type"] == "slot_freed" and e["slot"]["slot_id"] == slot_id), None)
    parts = []
    wait_s = None
    if taken:
        wait_s = round((confirmed["t_ms"] - taken["t_ms"]) / 1000, 1)
        parts.append(f"Ticket {served} waited {wait_s:.1f} s in line, from taking the ticket (event {taken['seq']}) to the booking (event {confirmed['seq']}).")
    if freed:
        parts.append(f"The slot came free at event {freed['seq']} and was booked {(confirmed['t_ms'] - freed['t_ms']) / 1000:.1f} s later.")
    if offered:
        parts.append(f"Its own offer took {(confirmed['t_ms'] - offered['t_ms']) / 1000:.1f} s from offer (event {offered['seq']}) to booking.")
    out["served"] = {"ticket": served, "wait_s": wait_s, "answer": " ".join(parts),
                     "evidence_seq": [e["seq"] for e in (taken, freed, offered, confirmed) if e]}

    acks = [e for e in events if e["type"] == "passed" and e.get("slot_id") == slot_id]
    answers = []
    evidence: list[int] = []
    for ack in acks:
        decision = next((e for e in reversed(events) if e["type"] == "decision" and e.get("offer_id") == ack["offer_id"] and e.get("decision") == "pass"), None)
        if decision is None:
            continue
        kept = f" It kept its place (position {ack['position']})." if ack.get("position") else ""
        answers.append(f"Ticket {ack['ticket']} passed because: {decision['reason']} (event {decision['seq']}, decided by {decision.get('source')}).{kept}")
        evidence += [decision["seq"], ack["seq"]]
    expired = [e for e in events if e["type"] == "lease_expired" and e.get("slot_id") == slot_id]
    for e in expired:
        answers.append(f"Ticket {e['ticket']} did not answer before its lease ran out (event {e['seq']}); miss {e['misses']} of {e['max_misses']}.")
        evidence.append(e["seq"])
    out["passed"] = {"ticket": acks[0]["ticket"] if acks else None,
                     "answer": " ".join(answers) or "Nobody passed on this slot; the first ticket asked took it.",
                     "evidence_seq": evidence}
    return out
