"""The transcript: every protocol message and state change, in order.

The page draws from this list in LIVE mode, and a saved copy of it is what
REPLAY mode plays back. Sinks (see integrations/) get a copy of each event.
A sink that fails is reported as an event of its own; it is never silent
and it never stops the line.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol


class EventSink(Protocol):
    name: str

    def append(self, event: dict[str, Any]) -> None: ...


class Transcript:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._t0 = time.monotonic()
        self._sinks: list[EventSink] = []
        self._sink_failed: set[str] = set()

    def add_sink(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def emit(self, type_: str, actor: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            event = {
                "seq": len(self._events) + 1,
                "t_ms": int((time.monotonic() - self._t0) * 1000),
                "type": type_,
                "actor": actor,
                **data,
            }
            self._events.append(event)
        for sink in self._sinks:
            try:
                sink.append(event)
            except Exception as exc:
                if sink.name not in self._sink_failed:  # report once, loudly
                    self._sink_failed.add(sink.name)
                    self.emit(
                        "integration_error",
                        sink.name,
                        message=f"{sink.name} event sink failed: {type(exc).__name__}: {str(exc)[:120]}",
                    )
        return event

    def since(self, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return self._events[after:]

    def all(self) -> list[dict[str, Any]]:
        return self.since(0)
