"""Time the line itself, no model and no network.

    uv run python scripts/bench_queue.py

Worst case for one slot: every ticket in the line passes, so each next
offer has to skip everyone who already passed.
"""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from take_a_number.queue import TicketLine  # noqa: E402


def run(n: int) -> None:
    tracemalloc.start()
    line = TicketLine()
    for i in range(n):
        line.take_ticket(f"agent-{i}")
    line.open_slot("s")
    t0 = time.perf_counter()
    first = None
    while (offer := line.next_offer("s", 60)) is not None:
        if first is None:
            first = time.perf_counter() - t0
        line.pass_(offer.offer_id, offer.ticket)
    total = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{n:>7} tickets: first offer {first * 1e6:8.1f} us, everyone passes in {total * 1e3:9.1f} ms, peak memory {peak / 1e6:6.1f} MB", flush=True)


if __name__ == "__main__":
    for n in [int(a) for a in sys.argv[1:]] or (100, 1_000, 10_000, 100_000):
        run(n)
