"""The only place a language model is called.

Two jobs use it: reading a free-text cancellation, and judging an offer
against a person's plain-language constraints. Everything else is plain code.

A call returns parsed JSON plus how long it took and which model answered.
If no provider answers in time it raises LLMUnavailable, and the caller must
handle that out loud (escalate, or hand to staff). Nothing here guesses.

Keys are read from the environment and are never logged. Error text is built
from status codes only, never from request headers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import config

TIMEOUT_S = 8.0  # per provider attempt
FORCE_UNAVAILABLE = False  # the demo's "sitter model outage" switch, read by the Line Sitters; always labelled on the page


class LLMUnavailable(Exception):
    pass


@dataclass
class LLMResult:
    data: dict[str, Any]
    provider: str
    model: str
    ms: int


def _loads(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply was not a JSON object")
    return data


async def _gemini(client: httpx.AsyncClient, system: str, user: str) -> tuple[str, str]:
    key = config.get("GEMINI_API_KEY")
    model = config.get("TAN_GEMINI_MODEL") or "gemini-3.8-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gen: dict[str, Any] = {
        "temperature": 0,
        "responseMimeType": "application/json",
        "maxOutputTokens": 1024,
        "thinkingConfig": {"thinkingBudget": 0},
    }
    for attempt in (1, 2):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        r = await client.post(url, headers={"x-goog-api-key": key}, json=body)
        if r.status_code == 400 and attempt == 1 and "thinkingConfig" in gen:
            gen = {k: v for k, v in gen.items() if k != "thinkingConfig"}
            continue  # some models reject the thinking switch; ask again without it
        if r.status_code != 200:
            raise RuntimeError(f"gemini http {r.status_code}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts), model
    raise RuntimeError("gemini gave no answer")


async def _openai(client: httpx.AsyncClient, system: str, user: str) -> tuple[str, str]:
    key = config.get("OPENAI_API_KEY")
    model = config.get("TAN_OPENAI_MODEL") or "gpt-4o-mini"
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"openai http {r.status_code}")
    return r.json()["choices"][0]["message"]["content"], model


async def _azure(client: httpx.AsyncClient, system: str, user: str) -> tuple[str, str]:
    key = config.get("AZURE_OPENAI_API_KEY")
    base = config.get("AZURE_OPENAI_BASE_URL").rstrip("/")
    model = config.get("TAN_AZURE_DEPLOYMENT") or "gpt-5-mini"
    r = await client.post(
        f"{base}/chat/completions",
        headers={"api-key": key, "Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"azure http {r.status_code}")
    return r.json()["choices"][0]["message"]["content"], model


def providers() -> list[str]:
    """Which providers have a key, in the order they are tried."""
    out = []
    if config.get("GEMINI_API_KEY"):
        out.append("gemini")
    if config.get("OPENAI_API_KEY"):
        out.append("openai")
    if config.get("AZURE_OPENAI_API_KEY") and config.get("AZURE_OPENAI_BASE_URL"):
        out.append("azure")
    return out


_CALLS = {"gemini": _gemini, "openai": _openai, "azure": _azure}


async def complete_json(system: str, user: str, timeout_s: float = TIMEOUT_S) -> LLMResult:
    """Ask for a JSON object. Tries each provider that has a key, in order."""
    errors: list[str] = []
    names = providers()
    if not names:
        raise LLMUnavailable("no model key is set")
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for name in names:
            # The first provider gets one second try: a model that returns a cut-off
            # reply once usually answers properly the next time.
            for attempt in range(2 if name == names[0] else 1):
                started = time.perf_counter()
                try:
                    text, model = await _CALLS[name](client, system, user)
                    data = _loads(text)
                except httpx.TimeoutException:
                    errors.append(f"{name}: timed out after {timeout_s:.0f}s")
                    break  # do not spend another full timeout on the same provider
                except Exception as exc:  # status errors, bad JSON, missing fields
                    errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:80]}")
                    continue
                ms = int((time.perf_counter() - started) * 1000)
                return LLMResult(data=data, provider=name, model=model, ms=ms)
    raise LLMUnavailable("; ".join(errors))
