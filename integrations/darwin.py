"""Darwin: a directory where agents are found by what they can do.

Use here: make the front desk findable, so a person's agent can ask
"where is the line for this clinic" and get back the desk's agent card.

This module builds the listing and shows the exact commands. It does not
submit anything. A public listing is the owner's call, so `listing()`
writes JSON and `commands()` prints what to run.

Shapes are from Darwin's published API files as read on 2026-10-11:
  create an agent    POST https://api.darwin.so/api/v1/ais
  create a listing   POST https://api.darwin.so/api/v1/ais/{aiId}/listings
  search (no key)    POST https://api.darwin.so/api/v3/search  {"query": "...", "maxResults": 5}
  auth header        x-api-key: <key>
Darwin's own index called publishing "coming soon" on that date, so
whether a new account can post a listing is not confirmed.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

API_V1 = "https://api.darwin.so/api/v1"
SEARCH_V3 = "https://api.darwin.so/api/v3/search"
SEARCH_V2 = "https://api.darwin.so/api/v2/search"


def status() -> dict[str, Any]:
    has_key = bool(os.environ.get("DARWIN_API_KEY"))
    return {"tool": "Darwin", "simulated": True, "listed": False, "has_key": has_key, "search": "real call, no key needed",
            "note": "Search is a real call. The listing file and commands are generated; nothing is submitted by this code."}


def listing(card: dict[str, Any], public_url: str) -> dict[str, Any]:
    """The two request bodies, built from the front desk's agent card."""
    public_url = public_url.rstrip("/")
    skills = card.get("skills", [])
    return {
        "ai": {
            "name": card["name"][:160],
            "type": "business",
            "description": card["description"],
            "visibility": "PUBLIC",
            "links": [f"{public_url}/.well-known/agent-card.json"],
            "capabilities": [{"id": s["id"], "name": s["name"], "description": s["description"]} for s in skills],
        },
        "listing": {
            "type": "CAPABILITY",
            "executionDomain": "DIGITAL",
            "capabilityKind": "API",
            "title": f"Take a ticket in the line at {card['name']}"[:200],
            "description": (
                "A waiting line that agents can join. POST take_ticket to get a numbered ticket. "
                "When an appointment is cancelled the desk offers the freed slot to the first ticket under a short lease; "
                "the ticket holder answers accept or pass, then confirms with an idempotency key. "
                f"Agent card: {public_url}/.well-known/agent-card.json"
            ),
            "status": "DRAFT",
            "visibility": "PUBLIC",
            "category": "scheduling",
            "tags": ["waitlist", "queue", "appointments", "take-a-number", "a2a"],
            "pricing": {"mode": "FREE"},
            "sellerAcceptancePolicy": {"mode": "INSTANT"},
            "attributes": {"protocol": card.get("protocolVersion"), "agentCard": f"{public_url}/.well-known/agent-card.json",
                           "leaseSeconds": card.get("takeANumber", {}).get("lease_seconds")},
        },
    }


def commands(path: str = "integrations/darwin_listing.json") -> list[str]:
    return [
        f"jq .ai {path} | curl -sS -X POST {API_V1}/ais -H \"x-api-key: $DARWIN_API_KEY\" -H 'Content-Type: application/json' --data @-",
        f"jq .listing {path} | curl -sS -X POST {API_V1}/ais/AI_ID_FROM_STEP_1/listings -H \"x-api-key: $DARWIN_API_KEY\" -H 'Content-Type: application/json' --data @-",
    ]


SEARCH_QUERY = "take a ticket and hold a place in line for a cancelled clinic appointment"


async def search(query: str = SEARCH_QUERY, max_results: int = 5) -> dict[str, Any]:
    """Ask Darwin who can do something. A real call; works without a key. Read-only.

    Tries the v3 endpoint from Darwin's quickstart, then the v2 endpoint from
    its OpenAPI file if v3 errors. Reports which one answered.
    """
    headers = {"x-api-key": os.environ["DARWIN_API_KEY"]} if os.environ.get("DARWIN_API_KEY") else {}
    out: dict[str, Any] = {"tool": "Darwin", "simulated": False, "query": query}
    attempts = (
        ("v3", SEARCH_V3, {"query": query[:2000], "maxResults": max_results}),
        ("v2", SEARCH_V2, {"query": query[:2000], "numResults": max_results}),
    )
    errors = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for name, url, body in attempts:
                r = await client.post(url, json=body, headers=headers)
                if r.status_code != 200:
                    errors.append(f"{name}: http {r.status_code}")
                    continue
                data = r.json()
                agents = (data.get("response") or {}).get("agents") or data.get("agents") or []
                names = [str(a.get("name") or a.get("agentSlug") or a.get("agentId") or a.get("agent") or "")[:60] for a in agents]
                return out | {"ok": True, "endpoint": name, "count": len(agents), "agents": names[:max_results],
                              "outcome": data.get("outcome") or data.get("status")}
    except httpx.HTTPError as exc:
        errors.append(type(exc).__name__)
    return out | {"ok": False, "error": "; ".join(errors)}
