"""Dial: a real phone number for an agent (SMS, iMessage, WhatsApp, voice).

Two uses here:

1. Out: when the Line Sitter must ask its person one yes/no question, it
   texts them. ("3:00 PM tomorrow opened up, held for 60 seconds. Reply Y
   to take it.")
2. In: a text to the front desk's number is read as a cancellation, and a
   Y or N from the person's number answers the pending question.

A real text is sent only when DIAL_API_KEY, DIAL_FROM_NUMBER and
DEMO_TO_NUMBER are all set. Otherwise nothing leaves this machine and the
return value says `"simulated": True`.

API shape, from docs.getdial.ai as read on 2026-10-11:
  POST https://api.getdial.ai/v1/messages
  Authorization: Bearer <key>
  {"to": "+1...", "fromNumber": "+1... or number id", "body": "..."}
  201 -> {"message": {"id": "msg_...", "status": "...", "deliveryState": "..."}}
Their guides use /v1 and their OpenAPI file uses /api/v1. This tries /v1
and falls back to /api/v1 only on a 404, which means nothing was sent.
Dial has no idempotency key for sends and no test mode, so there is no
retry here: one attempt, and a failure is reported, not repeated.

Inbound webhooks carry `X-Dial-Signature: t=<unix>,v1=<hex>`, an
HMAC-SHA256 of "{t}.{raw body}" with the webhook secret.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime
from typing import Any

import httpx

API_HOST = "https://api.getdial.ai"
NEEDED = ("DIAL_API_KEY", "DIAL_FROM_NUMBER", "DEMO_TO_NUMBER")


def is_live() -> bool:
    return all(os.environ.get(name) for name in NEEDED)


def status() -> dict[str, Any]:
    missing = [name for name in NEEDED if not os.environ.get(name)]
    return {"tool": "Dial", "simulated": bool(missing), "missing": missing,
            "inbound_verified": bool(os.environ.get("DIAL_WEBHOOK_SECRET"))}


def mask(number: str) -> str:
    return f"***{number[-4:]}" if len(number) >= 4 else "***"


def question_text(question: str, offer: dict[str, Any]) -> str:
    """One text: what opened, how long it is held, the one question, how to answer."""
    slot = offer.get("slot") or {}
    opened = ""
    if slot.get("start"):
        start = datetime.fromisoformat(slot["start"])
        opened = f"{slot.get('where', 'The front desk')}: {start.strftime('%a %-I:%M %p')} opened up"
        lease = offer.get("lease_seconds")
        opened += f", held for {int(lease)} seconds. " if lease else ". "
    return f"{opened}{question} Reply Y to take it, N to pass."[:320]


async def send_text(body: str) -> dict[str, Any]:
    """Send one SMS to DEMO_TO_NUMBER, or report that it was simulated."""
    if not is_live():
        return {"channel": "sms", "simulated": True, "body": body,
                "note": "Simulated: no text was sent. Set DIAL_API_KEY, DIAL_FROM_NUMBER and DEMO_TO_NUMBER to send a real one."}
    to = os.environ["DEMO_TO_NUMBER"]
    payload = {"to": to, "fromNumber": os.environ["DIAL_FROM_NUMBER"], "body": body, "channel": "sms"}
    headers = {"Authorization": f"Bearer {os.environ['DIAL_API_KEY']}"}
    out: dict[str, Any] = {"channel": "sms", "simulated": False, "to": mask(to), "body": body}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{API_HOST}/v1/messages", json=payload, headers=headers)
            if r.status_code == 404:  # nothing sent; try the other documented prefix
                r = await client.post(f"{API_HOST}/api/v1/messages", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        return out | {"sent": False, "error": type(exc).__name__}
    if r.status_code not in (200, 201):
        return out | {"sent": False, "error": f"http {r.status_code}"}
    message = (r.json() or {}).get("message", {})
    return out | {"sent": True, "message_id": message.get("id"),
                  "delivery_state": message.get("deliveryState") or message.get("status")}


async def ask_yes_no(person: Any, question: str, offer: dict[str, Any]) -> dict[str, Any]:
    """The Line Sitter's notifier. `person` is only used for the greeting; no calendar data is sent."""
    return await send_text(question_text(question, offer))


def verify_signature(raw_body: bytes, header: str, secret: str, max_age_s: int = 300, now: float | None = None) -> bool:
    try:
        parts = dict(piece.split("=", 1) for piece in header.split(","))
        stamp, given = parts["t"], parts["v1"]
        if abs((now if now is not None else time.time()) - int(stamp)) > max_age_s:
            return False
    except (KeyError, ValueError):
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def parse_inbound(event: dict[str, Any]) -> dict[str, Any] | None:
    """Pull {from, text, id} out of a message.received event. None for anything else."""
    if event.get("type") != "message.received":
        return None
    data = event.get("data") or {}
    text = (data.get("body") or "").strip()
    if not text or not data.get("from"):
        return None
    return {"from": data["from"], "text": text, "id": data.get("messageId") or event.get("id")}


def yes_no(text: str) -> bool | None:
    word = text.strip().lower().strip(".!")
    if word in ("y", "yes", "yeah", "yep", "ok", "take it"):
        return True
    if word in ("n", "no", "nope", "pass"):
        return False
    return None
