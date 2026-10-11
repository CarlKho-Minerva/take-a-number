"""Read settings from the environment. Values are never printed or logged."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Only these names are ever copied from an env file into the process.
ALLOWED = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_BASE_URL",
    "TAN_GEMINI_MODEL",
    "TAN_OPENAI_MODEL",
    "TAN_AZURE_DEPLOYMENT",
    "DIAL_API_KEY",
    "DIAL_FROM_NUMBER",
    "DEMO_TO_NUMBER",
    "DARWIN_API_KEY",
    "KEEWANO_URL",
)


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, value = line.split("=", 1)
        out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def load_env() -> list[str]:
    """Load ./.env, then any files named in TAN_ENV_FILE (colon separated).

    Real environment variables win. Returns the names that were set, never
    the values.
    """
    paths = [ROOT / ".env"]
    for extra in os.environ.get("TAN_ENV_FILE", "").split(":"):
        if extra.strip():
            paths.append(Path(extra.strip()).expanduser())
    loaded: list[str] = []
    for path in paths:
        for name, value in _parse(path).items():
            if name in ALLOWED and value and not os.environ.get(name):
                os.environ[name] = value
                loaded.append(name)
    return loaded


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
