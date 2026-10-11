"""Record real LIVE runs into docs/replay.json.

Start the server first, then:  uv run python scripts/record.py
It drives the same endpoints the page does and saves the transcripts as
they happened. Nothing is edited afterwards.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8041"
OUT = Path(__file__).resolve().parent.parent / "docs" / "replay.json"

MARIA = "hi this is Maria I can't make my 3pm tomorrow"
TOM = "This is Tom. Something came up, please cancel my 9:30 tomorrow morning."

# answers: what each person says if their agent asks them. None means nobody answers.
RUNS = [
    {"id": "maria-3pm", "label": "Maria cancels 3:00 PM", "text": MARIA, "lease_seconds": 60, "answers": {}},
    {"id": "tom-930", "label": "Tom cancels 9:30 AM (agents must ask)", "text": TOM, "lease_seconds": 60,
     "answers": {"dev": False, "ana": False, "sam": True}},
    {"id": "lease-out", "label": "Nobody answers: the lease runs out", "text": TOM, "lease_seconds": 10,
     "answers": {"dev": None, "ana": False, "sam": True}},
]


def record(client: httpx.Client, spec: dict) -> dict:
    client.post(f"{BASE}/demo/reset", json={"lease_seconds": spec["lease_seconds"], "retry_confirm": True}).raise_for_status()
    client.post(f"{BASE}/demo/cancel", json={"text": spec["text"]}).raise_for_status()
    seen, deadline, done = 0, time.time() + 120, False
    while time.time() < deadline and not done:
        events = client.get(f"{BASE}/demo/events", params={"after": seen}).json()["events"]
        for e in events:
            seen = e["seq"]
            if e["type"] == "escalation":
                pid = e["actor"].split(":")[1]
                answer = spec["answers"].get(pid)
                if answer is not None:
                    time.sleep(1.8)  # a person reading the question
                    client.post(f"{BASE}/sitters/{pid}/human/answer", json={"answer": answer}).raise_for_status()
            if e["type"] in ("slot_unfilled", "needs_staff", "agent_error") or (e["type"] == "confirmed" and e.get("replayed")):
                done = True
        time.sleep(0.15)
    time.sleep(0.5)
    transcript = client.get(f"{BASE}/demo/transcript").json()
    types = [e["type"] for e in transcript["events"]]
    if not done or "agent_error" in types:
        raise SystemExit(f"run {spec['id']} did not finish cleanly: {types}")
    print(f"{spec['id']}: {len(types)} events, last T={transcript['events'][-1]['t_ms']} ms")
    return {k: spec[k] for k in ("id", "label", "text", "lease_seconds")} | {"events": transcript["events"], "answer": transcript["answer"]}


def main() -> None:
    with httpx.Client(timeout=30) as client:
        health = client.get(f"{BASE}/demo/health").json()
        if not health.get("model_providers"):
            raise SystemExit("no model key is set on the server; a recording must come from real model calls")
        runs = [record(client, spec) for spec in RUNS]
    OUT.write_text(json.dumps({
        "recorded_at": time.strftime("%Y-%m-%d %H:%M %Z"),
        "note": "Real LIVE runs: real HTTP between the agents on one machine, real model calls. Saved by scripts/record.py, not edited.",
        "runs": runs,
    }, indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
