"""Write integrations/darwin_listing.json from the front desk's agent card.

    uv run python scripts/darwin_listing.py https://your-public-host

It writes a file and prints the two commands that would submit it. It does
not submit anything; publishing a public listing is the owner's decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations import darwin  # noqa: E402
from take_a_number import scenario  # noqa: E402
from take_a_number.events import Transcript  # noqa: E402
from take_a_number.front_desk import FrontDesk  # noqa: E402


def main() -> None:
    public = sys.argv[1] if len(sys.argv) > 1 else "https://YOUR-PUBLIC-HOST"
    day0 = scenario.today()
    desk = FrontDesk(scenario.CLINIC, f"{public}/desk", scenario.schedule(day0), Transcript(), lambda: None, day0)  # type: ignore[arg-type]
    out = ROOT / "integrations" / "darwin_listing.json"
    out.write_text(json.dumps(darwin.listing(desk.agent_card(), public), indent=2) + "\n")
    print(f"wrote {out}")
    print("To submit (needs DARWIN_API_KEY and a public https URL for the desk):")
    for command in darwin.commands():
        print("  " + command)


if __name__ == "__main__":
    main()
