"""One process that hosts the front desk, three line sitters, and the demo page.

The agents still talk to each other over real HTTP on the loopback socket:
the desk POSTs an offer to a sitter's URL, the sitter POSTs accept, pass
and confirm back. Each agent publishes its own agent card.

  /.well-known/agent-card.json           the front desk's card (agent.json also works)
  /desk/a2a/...                           take_ticket, position, accept, pass, confirm
  /sitters/{id}/a2a/offer                 where the desk pushes offers
  /sitters/{id}/.well-known/agent-card.json
  /demo/...                               drives the demo page
  /integrations/dial/webhook              inbound texts
  /                                       the page (docs/)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from integrations import darwin, dial, keewano
from integrations.eventlog import JsonlEventLog

from . import config, llm, scenario
from .events import Transcript
from .front_desk import FrontDesk, error_body
from .line_sitter import LineSitter
from .queue import LeaseExpired, LineError

config.load_env()

ClientFactory = Any


class World:
    """Everything one demo run needs. Rebuilt on every reset."""

    def __init__(self, base_url: str, client_factory: ClientFactory, lease_seconds: float = 60.0,
                 retry_confirm: bool = True, log_dir: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.transcript = Transcript()
        self.log = JsonlEventLog(Path(log_dir or os.environ.get("TAN_LOG_DIR") or config.ROOT / "data") / "events.jsonl")
        self.transcript.add_sink(self.log)
        day0 = scenario.today()
        self.desk = FrontDesk(
            name=scenario.CLINIC, base_url=f"{self.base_url}/desk", schedule=scenario.schedule(day0),
            transcript=self.transcript, client_factory=client_factory, today=day0, lease_seconds=lease_seconds,
        )
        self.sitters: dict[str, LineSitter] = {
            p.id: LineSitter(p, f"{self.base_url}/sitters/{p.id}", self.transcript, client_factory,
                             notifier=dial.ask_yes_no, retry_confirm_for_demo=retry_confirm)
            for p in scenario.people(day0)
        }
        self.lease_seconds = lease_seconds
        self._reported_expired: set[str] = set()

    async def open(self) -> None:
        self.transcript.emit(
            "reset", "demo", clinic=scenario.CLINIC, today=self.desk.today.isoformat(),
            schedule=[dict(a) for a in self.desk.schedule], people=[s.person.public() for s in self.sitters.values()],
            lease_seconds=self.lease_seconds, max_misses=self.desk.line.max_misses,
            integrations=integration_status(self), model_providers=llm.providers(), model_forced_off=llm.FORCE_UNAVAILABLE,
        )
        for sitter in self.sitters.values():  # each sitter takes its own ticket, over HTTP
            await sitter.take_ticket(self.desk.base_url)

    async def close(self) -> None:
        await self.desk.close()
        for sitter in self.sitters.values():
            await sitter.close()


def integration_status(world: World) -> dict[str, Any]:
    return {"dial": dial.status(), "darwin": darwin.status(), "keewano": keewano.backend(world.log)}


def default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


class State:
    world: World | None = None
    client_factory: ClientFactory = staticmethod(default_client)
    sweeper: asyncio.Task[Any] | None = None
    background: set[asyncio.Task[Any]] = set()


state = State()


def world() -> World:
    if state.world is None:
        base = os.environ.get("TAN_BASE_URL", "http://127.0.0.1:8041")
        state.world = World(base, state.client_factory)
    return state.world


async def _sweep_forever() -> None:
    while True:
        await asyncio.sleep(0.25)
        w = state.world
        if w is None:
            continue
        try:
            await w.desk.sweep()
        except Exception as exc:  # never die quietly: say it on the page, keep sweeping
            w.transcript.emit("agent_error", "desk", message=f"lease sweeper: {type(exc).__name__}: {str(exc)[:160]}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.sweeper = asyncio.create_task(_sweep_forever())
    yield
    state.sweeper.cancel()
    if state.world is not None:
        await state.world.close()


app = FastAPI(title="Take a Number", version="0.1.0", lifespan=lifespan)


@app.exception_handler(LineError)
async def line_error_handler(_: Request, exc: LineError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=error_body(exc))


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    state.background.add(task)

    def done(t: asyncio.Task[Any]) -> None:
        state.background.discard(t)
        if not t.cancelled() and t.exception() is not None and state.world is not None:
            exc = t.exception()
            state.world.transcript.emit("agent_error", "demo", message=f"{type(exc).__name__}: {str(exc)[:160]}")

    task.add_done_callback(done)


# ---- agent cards ---------------------------------------------------------


@app.get("/.well-known/agent-card.json")
@app.get("/.well-known/agent.json")
@app.get("/desk/.well-known/agent-card.json")
@app.get("/desk/.well-known/agent.json")
async def desk_card() -> dict[str, Any]:
    return world().desk.agent_card()


def _sitter(pid: str) -> LineSitter:
    sitter = world().sitters.get(pid)
    if sitter is None:
        raise HTTPException(404, f"no line sitter '{pid}'")
    return sitter


@app.get("/sitters/{pid}/.well-known/agent-card.json")
@app.get("/sitters/{pid}/.well-known/agent.json")
async def sitter_card(pid: str) -> dict[str, Any]:
    return _sitter(pid).agent_card()


# ---- front desk: the protocol --------------------------------------------


class TakeTicket(BaseModel):
    agent_url: str = Field(min_length=8, max_length=300)
    label: str = Field(default="", max_length=60)


class TicketAuth(BaseModel):
    ticket: int
    ticket_secret: str = ""
    offer_id: str = Field(max_length=80)


class PassBody(TicketAuth):
    reason_code: str = Field(default="", max_length=40)


class ConfirmBody(TicketAuth):
    idempotency_key: str = Field(min_length=1, max_length=120)


def _auth(body: TicketAuth) -> FrontDesk:
    desk = world().desk
    if not desk.check_secret(body.ticket, body.ticket_secret):
        raise HTTPException(403, "ticket secret does not match")
    return desk


def _on_lease_expired(desk: FrontDesk, w: World, offer_id: str) -> None:
    """An answer arrived after the lease ran out, before the sweeper saw it. Report it and move the slot on."""
    if offer_id in w._reported_expired:
        return
    w._reported_expired.add(offer_id)
    offer = desk.line.offer(offer_id)
    t = desk.line.ticket(offer.ticket)
    w.transcript.emit("lease_expired", "desk", ticket=offer.ticket, offer_id=offer_id, slot_id=offer.slot_id,
                      misses=t.misses, max_misses=desk.line.max_misses, ticket_status=t.status,
                      position=desk.line.position(offer.ticket), noticed_by="late answer")
    _spawn(desk.advance(offer.slot_id))


@app.post("/desk/a2a/take_ticket")
async def take_ticket(body: TakeTicket) -> dict[str, Any]:
    return world().desk.take_ticket(body.agent_url, body.label)


@app.get("/desk/a2a/position/{ticket}")
async def position(ticket: int) -> dict[str, Any]:
    return world().desk.position(ticket)


@app.post("/desk/a2a/accept")
async def accept(body: TicketAuth) -> dict[str, Any]:
    desk = _auth(body)
    try:
        return desk.accept(body.ticket, body.offer_id)
    except LeaseExpired:
        _on_lease_expired(desk, world(), body.offer_id)
        raise


@app.post("/desk/a2a/pass")
async def pass_(body: PassBody) -> dict[str, Any]:
    desk = _auth(body)
    try:
        return desk.pass_(body.ticket, body.offer_id, body.reason_code)
    except LeaseExpired:
        _on_lease_expired(desk, world(), body.offer_id)
        raise


@app.post("/desk/a2a/confirm")
async def confirm(body: ConfirmBody) -> dict[str, Any]:
    desk = _auth(body)
    try:
        return desk.confirm(body.ticket, body.offer_id, body.idempotency_key)
    except LeaseExpired:
        _on_lease_expired(desk, world(), body.offer_id)
        raise


# ---- line sitters --------------------------------------------------------


class OfferBody(BaseModel):
    verb: str = "offer"
    offer_id: str
    ticket: int
    slot: dict[str, Any]
    lease_seconds: float
    reply_to: str = ""


class HumanAnswer(BaseModel):
    answer: bool
    offer_id: str | None = None


@app.post("/sitters/{pid}/a2a/offer")
async def offer(pid: str, body: OfferBody) -> dict[str, Any]:
    _sitter(pid).on_offer(body.model_dump())
    return {"received": True}


@app.post("/sitters/{pid}/human/answer")
async def human_answer(pid: str, body: HumanAnswer) -> dict[str, Any]:
    return {"answered": _sitter(pid).human_answer(body.answer, body.offer_id)}


# ---- demo controls -------------------------------------------------------


class ResetBody(BaseModel):
    lease_seconds: float = Field(default=60.0, gt=0.5, le=600)
    retry_confirm: bool = True
    model_off: bool = False


class CancelBody(BaseModel):
    text: str = Field(max_length=500)


@app.get("/demo/health")
async def health() -> dict[str, Any]:
    w = world()
    return {"ok": True, "model_providers": llm.providers(), "model_forced_off": llm.FORCE_UNAVAILABLE,
            "integrations": integration_status(w), "presets": scenario.PRESETS}


@app.post("/demo/reset")
async def reset(body: ResetBody, request: Request) -> dict[str, Any]:
    if state.world is not None:
        await state.world.close()
    llm.FORCE_UNAVAILABLE = body.model_off
    base = os.environ.get("TAN_BASE_URL") or str(request.base_url).rstrip("/")
    state.world = World(base, state.client_factory, lease_seconds=body.lease_seconds, retry_confirm=body.retry_confirm)
    await state.world.open()
    return {"ok": True, "events": len(state.world.transcript.all())}


@app.post("/demo/cancel")
async def cancel(body: CancelBody) -> dict[str, Any]:
    _spawn(world().desk.handle_cancellation(body.text, channel="page"))
    return {"accepted": True}


@app.get("/demo/events")
async def events(after: int = 0) -> dict[str, Any]:
    w = world()
    return {"events": w.transcript.since(max(0, after)), "server_ms": int(time.time() * 1000)}


@app.get("/demo/transcript")
async def transcript() -> dict[str, Any]:
    w = world()
    return {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "A real LIVE run: real HTTP between agents on one machine, real model calls. Saved by scripts/record.py.",
        "events": w.transcript.all(),
        "answer": keewano.how_long_and_why(w.log),
    }


@app.get("/demo/ask")
async def ask() -> dict[str, Any]:
    return keewano.how_long_and_why(world().log)


@app.get("/demo/line")
async def line_state() -> dict[str, Any]:
    return world().desk.line.snapshot()


@app.get("/demo/darwin")
async def darwin_listing(request: Request) -> dict[str, Any]:
    w = world()
    public = os.environ.get("TAN_PUBLIC_URL") or str(request.base_url).rstrip("/")
    return {"status": darwin.status(), "listing": darwin.listing(w.desk.agent_card(), public), "commands": darwin.commands()}


@app.get("/demo/darwin/search")
async def darwin_search() -> dict[str, Any]:
    """One real, read-only search of Darwin's directory: is there a line like this to find?"""
    return await darwin.search()


# ---- Dial inbound --------------------------------------------------------


@app.post("/integrations/dial/webhook")
async def dial_webhook(request: Request) -> dict[str, Any]:
    raw = await request.body()
    secret = os.environ.get("DIAL_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "inbound texts are off: DIAL_WEBHOOK_SECRET is not set, so deliveries cannot be verified")
    if not dial.verify_signature(raw, request.headers.get("X-Dial-Signature", ""), secret):
        raise HTTPException(401, "bad signature")
    try:
        message = dial.parse_inbound(json.loads(raw))
    except ValueError:
        raise HTTPException(400, "body is not JSON") from None
    if message is None:
        return {"ignored": True}
    return await route_text(message["from"], message["text"], simulated=False)


class SimulatedText(BaseModel):
    text: str = Field(max_length=500)
    from_person: str | None = None  # a sitter id, when the person is answering


@app.post("/demo/text")
async def simulated_text(body: SimulatedText) -> dict[str, Any]:
    """The page's stand-in for an inbound text. Marked simulated in the transcript."""
    return await route_text(body.from_person or "page", body.text, simulated=True)


async def route_text(sender: str, text: str, simulated: bool) -> dict[str, Any]:
    """A Y or N answers a waiting question. Anything else is read by the front desk."""
    w = world()
    answer = dial.yes_no(text)
    if answer is not None:
        waiting = [s for s in w.sitters.values() if s.pending_questions()]
        named = [s for s in waiting if s.person.id == sender]
        demo_number = os.environ.get("DEMO_TO_NUMBER", "")
        if not simulated and (not demo_number or sender != demo_number):
            waiting = []  # a real text only answers if it came from the person's own number
        target = (named or waiting)[:1]
        if target:
            w.transcript.emit("text_in", "dial", text=text, simulated=simulated, routed_to=target[0].actor)
            return {"answered": target[0].human_answer(answer)}
    w.transcript.emit("text_in", "dial", text=text, simulated=simulated, routed_to="desk")
    _spawn(w.desk.handle_cancellation(text, channel="sms (simulated)" if simulated else "sms"))
    return {"accepted": True}


app.mount("/", StaticFiles(directory=str(config.ROOT / "docs"), html=True), name="page")
