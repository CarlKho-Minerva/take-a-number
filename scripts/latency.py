"""Measure one offer round against a running server, with real model calls.

    uv run python scripts/latency.py [runs]

An offer round is: the desk sends an offer, the sitter decides, the desk has
the answer. Times come from the transcript's own clock (server side).
"""

from __future__ import annotations

import statistics
import sys
import time

import httpx

BASE = "http://127.0.0.1:8041"
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
TEXT = "hi this is Maria I can't make my 3pm tomorrow"


def one(client: httpx.Client) -> dict[str, float]:
    client.post(f"{BASE}/demo/reset", json={"lease_seconds": 60, "retry_confirm": False}).raise_for_status()
    client.post(f"{BASE}/demo/cancel", json={"text": TEXT}).raise_for_status()
    deadline = time.time() + 60
    while time.time() < deadline:
        events = client.get(f"{BASE}/demo/events").json()["events"]
        if any(e["type"] in ("confirmed", "needs_staff", "slot_unfilled") for e in events):
            break
        time.sleep(0.1)
    by = lambda t, **kw: next(e for e in events if e["type"] == t and all(e.get(k) == v for k, v in kw.items()))  # noqa: E731
    offers = [e for e in events if e["type"] == "offer"]
    decisions = [e for e in events if e["type"] == "decision"]
    first_answer = by("passed") if decisions[0]["decision"] == "pass" else by("held")
    return {
        "read_cancellation_ms": by("cancellation_parsed")["ms"],
        "offer_round_pass_ms": first_answer["t_ms"] - offers[0]["t_ms"],
        "of_which_model_ms": decisions[0]["ms"] or 0,
        "offer_to_booked_ms": by("confirmed")["t_ms"] - offers[-1]["t_ms"],
        "message_to_booked_ms": by("confirmed")["t_ms"] - by("cancellation_received")["t_ms"],
        "protocol_overhead_ms": (first_answer["t_ms"] - offers[0]["t_ms"]) - (decisions[0]["ms"] or 0),
    }


def main() -> None:
    rows = []
    with httpx.Client(timeout=30) as client:
        for i in range(RUNS):
            rows.append(one(client))
            print(i + 1, rows[-1])
    print(f"\nmedian over {RUNS} runs (min to max):")
    for key in rows[0]:
        values = [r[key] for r in rows]
        print(f"  {key:<24} {statistics.median(values):>7.0f} ms  ({min(values):.0f} to {max(values):.0f})")


if __name__ == "__main__":
    main()
