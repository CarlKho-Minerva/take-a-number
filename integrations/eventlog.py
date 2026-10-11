"""An append-only log of every protocol event, behind one small interface.

The interface is what matters: `append(event)` and `events()`. The backend
here is a JSONL file. A sequence database (KeewanoDB is the one this seam
was cut for) can replace it without touching the agents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class EventLog(Protocol):
    name: str
    backend: str

    def append(self, event: dict[str, Any]) -> None: ...

    def events(self) -> list[dict[str, Any]]: ...


class JsonlEventLog:
    name = "eventlog"
    backend = "jsonl"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")  # one file per run

    def append(self, event: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    def events(self) -> list[dict[str, Any]]:
        out = []
        with self.path.open() as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
        return out
