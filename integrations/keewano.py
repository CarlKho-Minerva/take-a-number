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


def how_long_and_why(log: EventLog, served: int | None = None, passed: int | None = None) -> dict[str, Any]:
    """How long did the served ticket wait, and why did the other one pass?

    Both tickets default to whatever the log itself shows. Every number in
    the answer carries the seq numbers of the events it was read from.
    """
    events = log.events()
    if served is None:
        served = next((e["ticket"] for e in events if e["type"] == "confirmed" and not e.get("replayed")), None)
    if passed is None:
        passed = next((e["ticket"] for e in events if e["type"] == "passed"), None)
    out: dict[str, Any] = {"backend": log.backend, "simulated_keewano": True, "events_read": len(events)}

    if served is not None:
        story = ticket_story(events, served)
        taken = next((e for e in story if e["type"] == "ticket_taken"), None)
        offered = next((e for e in story if e["type"] == "offer"), None)
        confirmed = next((e for e in story if e["type"] == "confirmed" and not e.get("replayed")), None)
        freed = next((e for e in events if e["type"] == "slot_freed"), None)
        if taken and confirmed:
            wait_s = (confirmed["t_ms"] - taken["t_ms"]) / 1000
            parts = [f"Ticket {served} waited {wait_s:.1f} s in line, from taking the ticket (event {taken['seq']}) to the booking (event {confirmed['seq']})."]
            if freed:
                parts.append(f"The slot came free at event {freed['seq']}; it was booked {(confirmed['t_ms'] - freed['t_ms']) / 1000:.1f} s later.")
            if offered:
                parts.append(f"Its own offer took {(confirmed['t_ms'] - offered['t_ms']) / 1000:.1f} s from offer (event {offered['seq']}) to booking.")
            out["served"] = {"ticket": served, "wait_s": round(wait_s, 1), "answer": " ".join(parts),
                             "evidence_seq": [e["seq"] for e in (taken, freed, offered, confirmed) if e]}
        else:
            out["served"] = {"ticket": served, "answer": f"The log does not show ticket {served} being booked."}
    else:
        out["served"] = {"ticket": None, "answer": "The log shows no booking yet."}

    if passed is not None:
        story = ticket_story(events, passed)
        decision = next((e for e in story if e["type"] == "decision" and e.get("decision") == "pass"), None)
        ack = next((e for e in story if e["type"] == "passed"), None)
        if decision:
            kept = f" It kept its place (position {ack['position']})." if ack and ack.get("position") else ""
            out["passed"] = {"ticket": passed, "answer": f"Ticket {passed} passed because: {decision['reason']} (event {decision['seq']}, decided by {decision.get('source')}).{kept}",
                             "evidence_seq": [e["seq"] for e in (decision, ack) if e]}
        else:
            out["passed"] = {"ticket": passed, "answer": f"The log does not show a pass decision for ticket {passed}."}
    else:
        out["passed"] = {"ticket": None, "answer": "The log shows no pass yet."}
    return out
