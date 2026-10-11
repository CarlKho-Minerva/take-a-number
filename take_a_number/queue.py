"""The line.

Plain code. No model, no network, and no wall clock: the clock is passed in,
so every rule here can be tested by moving time by hand.

Rules, in one place:

* Tickets are served in the order they were taken. One ticket per holder.
* A freed slot is offered to one ticket at a time, under a lease.
* While the lease runs, nobody else can be offered that slot.
* accept  : "hold it for me". Does not book yet.
* confirm : books. Carries an idempotency key, so a retry returns the same
            booking instead of making a second one.
* pass    : "not this one". The slot goes to the next ticket. The passer
            keeps their ticket and their place for every later slot.
* A lease that runs out counts as a miss. The slot goes to the next ticket.
  The ticket keeps its place, until it has missed `max_misses` times, at
  which point it is marked no_show and leaves the line.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


class LineError(Exception):
    """Base class. `code` is what goes over the wire."""

    code = "line_error"
    http_status = 400


class UnknownTicket(LineError):
    code = "unknown_ticket"
    http_status = 404


class UnknownSlot(LineError):
    code = "unknown_slot"
    http_status = 404


class UnknownOffer(LineError):
    code = "unknown_offer"
    http_status = 404


class NotYourOffer(LineError):
    code = "not_your_offer"
    http_status = 403


class LeaseExpired(LineError):
    code = "lease_expired"
    http_status = 410


class WrongState(LineError):
    code = "wrong_state"
    http_status = 409


class IdempotencyConflict(LineError):
    code = "idempotency_conflict"
    http_status = 409


@dataclass
class Ticket:
    number: int
    holder: str
    taken_at: float
    status: str = "waiting"  # waiting | served | no_show | left
    misses: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Slot:
    slot_id: str
    info: dict[str, Any]
    opened_at: float
    status: str = "open"  # open | offered | booked | unfilled
    tried: dict[int, None] = field(default_factory=dict)  # tickets that passed or missed, in order; a dict so lookup is O(1)
    cursor: int = 0  # every ticket before this index in the line can never be offered this slot
    booked_by: int | None = None


@dataclass
class Offer:
    offer_id: str
    slot_id: str
    ticket: int
    issued_at: float
    expires_at: float
    status: str = "offered"  # offered | accepted | passed | expired | confirmed


@dataclass
class Booking:
    booking_id: str
    slot_id: str
    offer_id: str
    ticket: int
    idempotency_key: str
    confirmed_at: float


class TicketLine:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        first_number: int = 1,
        max_misses: int = 2,
    ) -> None:
        self._clock = clock
        self._next_number = first_number
        self.max_misses = max_misses
        self._lock = threading.RLock()
        self._tickets: dict[int, Ticket] = {}  # insertion order is line order
        self._order: list[int] = []  # ticket numbers in line order, append-only
        self._by_holder: dict[str, int] = {}
        self._slots: dict[str, Slot] = {}
        self._offers: dict[str, Offer] = {}
        self._active_offer_by_ticket: dict[int, str] = {}
        self._active_offer_by_slot: dict[str, str] = {}
        self._bookings_by_key: dict[str, Booking] = {}
        self._offer_seq = 0
        self._booking_seq = 0

    # ---- tickets -------------------------------------------------------

    def take_ticket(self, holder: str, meta: dict[str, Any] | None = None) -> Ticket:
        """Join the line. Asking twice returns the ticket you already hold."""
        if not holder:
            raise WrongState("a ticket needs a holder")
        with self._lock:
            existing = self._by_holder.get(holder)
            if existing is not None and self._tickets[existing].status == "waiting":
                return self._tickets[existing]
            ticket = Ticket(
                number=self._next_number,
                holder=holder,
                taken_at=self._clock(),
                meta=dict(meta or {}),
            )
            self._next_number += 1
            self._tickets[ticket.number] = ticket
            self._by_holder[holder] = ticket.number
            self._order.append(ticket.number)
            return ticket

    def ticket(self, number: int) -> Ticket:
        with self._lock:
            try:
                return self._tickets[number]
            except KeyError:
                raise UnknownTicket(f"no ticket {number}") from None

    def waiting(self) -> list[int]:
        with self._lock:
            return [n for n, t in self._tickets.items() if t.status == "waiting"]

    def position(self, number: int) -> int | None:
        """1 means next. None means the ticket is no longer in the line."""
        with self._lock:
            ticket = self.ticket(number)
            if ticket.status != "waiting":
                return None
            return self.waiting().index(number) + 1

    def leave(self, number: int) -> Ticket:
        with self._lock:
            ticket = self.ticket(number)
            if ticket.status == "waiting":
                active = self._active_offer_by_ticket.get(number)
                if active:
                    self._release(self._offers[active], "passed", count_miss=False)
                ticket.status = "left"
            return ticket

    # ---- slots and offers ----------------------------------------------

    def open_slot(self, slot_id: str, info: dict[str, Any] | None = None) -> Slot:
        """A slot came free. Opening the same slot twice is a no-op."""
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot is not None and slot.status != "unfilled":
                return slot
            if slot is None:
                slot = Slot(slot_id=slot_id, info=dict(info or {}), opened_at=self._clock())
                self._slots[slot_id] = slot
            else:
                slot.status = "open"
            return slot

    def slot(self, slot_id: str) -> Slot:
        with self._lock:
            try:
                return self._slots[slot_id]
            except KeyError:
                raise UnknownSlot(f"no slot {slot_id}") from None

    def next_offer(self, slot_id: str, lease_seconds: float) -> Offer | None:
        """Offer the slot to the first ticket that has not yet passed or missed it.

        Returns None when there is nobody left to ask (the slot is then marked
        unfilled) or when the slot is already out on a lease or booked. It does
        not sweep expired leases itself: call expire_due() for that, so the
        caller sees every expiry and can report it.
        """
        if lease_seconds <= 0:
            raise WrongState("lease_seconds must be positive")
        with self._lock:
            slot = self.slot(slot_id)
            if slot.status != "open":
                return None
            busy_waiting = False
            chosen: Ticket | None = None
            # Scan from this slot's cursor, not from the front. A ticket that has left the
            # line or already turned this slot down can never be offered it, so the cursor
            # moves past it for good. That keeps "everyone passes" linear instead of quadratic.
            advancing = True
            index = slot.cursor
            while index < len(self._order):
                number = self._order[index]
                ticket = self._tickets[number]
                index += 1
                if ticket.status != "waiting" or number in slot.tried:
                    if advancing:
                        slot.cursor = index
                    continue
                advancing = False
                if number in self._active_offer_by_ticket:
                    busy_waiting = True  # deciding on another slot; ask later
                    continue
                chosen = ticket
                break
            if chosen is None:
                if not busy_waiting:
                    slot.status = "unfilled"
                return None
            now = self._clock()
            self._offer_seq += 1
            offer = Offer(
                offer_id=f"offer-{self._offer_seq}",
                slot_id=slot_id,
                ticket=chosen.number,
                issued_at=now,
                expires_at=now + lease_seconds,
            )
            self._offers[offer.offer_id] = offer
            self._active_offer_by_ticket[chosen.number] = offer.offer_id
            self._active_offer_by_slot[slot_id] = offer.offer_id
            slot.status = "offered"
            return offer

    def open_slots(self) -> list[str]:
        """Slots that are free and not out on a lease right now (for example, everyone
        left to ask was busy with another slot)."""
        with self._lock:
            return [sid for sid, slot in self._slots.items() if slot.status == "open"]

    def offer(self, offer_id: str) -> Offer:
        with self._lock:
            try:
                return self._offers[offer_id]
            except KeyError:
                raise UnknownOffer(f"no offer {offer_id}") from None

    def accept(self, offer_id: str, ticket: int) -> Offer:
        """Hold the slot. Must arrive before the lease runs out."""
        with self._lock:
            offer = self._live_offer(offer_id, ticket)
            if offer.status == "accepted":
                return offer  # a repeated accept is harmless
            if offer.status != "offered":
                raise WrongState(f"offer is {offer.status}, cannot accept")
            offer.status = "accepted"
            return offer

    def pass_(self, offer_id: str, ticket: int) -> Offer:
        """Turn the slot down. The ticket keeps its place for later slots."""
        with self._lock:
            offer = self._live_offer(offer_id, ticket)
            if offer.status == "passed":
                return offer
            if offer.status not in ("offered", "accepted"):
                raise WrongState(f"offer is {offer.status}, cannot pass")
            self._release(offer, "passed", count_miss=False)
            return offer

    def confirm(self, offer_id: str, ticket: int, idempotency_key: str) -> tuple[Booking, bool]:
        """Book the held slot. Returns (booking, replayed).

        The key is checked first, before the lease and before the state, so a
        retry that arrives late still gets the original booking back.
        """
        if not idempotency_key:
            raise WrongState("confirm needs an idempotency key")
        with self._lock:
            prior = self._bookings_by_key.get(idempotency_key)
            if prior is not None:
                if prior.offer_id != offer_id or prior.ticket != ticket:
                    raise IdempotencyConflict("this key was already used for a different offer")
                return prior, True
            offer = self._live_offer(offer_id, ticket)
            if offer.status == "confirmed":
                raise IdempotencyConflict("this offer was already confirmed with a different key")
            if offer.status != "accepted":
                raise WrongState(f"offer is {offer.status}; accept it before confirming")
            slot = self._slots[offer.slot_id]
            self._booking_seq += 1
            booking = Booking(
                booking_id=f"booking-{self._booking_seq}",
                slot_id=offer.slot_id,
                offer_id=offer_id,
                ticket=ticket,
                idempotency_key=idempotency_key,
                confirmed_at=self._clock(),
            )
            self._bookings_by_key[idempotency_key] = booking
            offer.status = "confirmed"
            slot.status = "booked"
            slot.booked_by = ticket
            self._tickets[ticket].status = "served"
            self._active_offer_by_ticket.pop(ticket, None)
            self._active_offer_by_slot.pop(offer.slot_id, None)
            return booking, False

    def expire_due(self) -> list[Offer]:
        """Release every lease that has run out. Safe to call as often as you like."""
        with self._lock:
            now = self._clock()
            due = [
                self._offers[oid]
                for oid in list(self._active_offer_by_slot.values())
                if self._offers[oid].expires_at <= now
            ]
            for offer in due:
                self._release(offer, "expired", count_miss=True)
            return due

    def fail_delivery(self, offer_id: str) -> Offer:
        """The offer could not be delivered (agent unreachable). Counts as a miss."""
        with self._lock:
            offer = self.offer(offer_id)
            if offer.status in ("offered", "accepted"):
                self._release(offer, "expired", count_miss=True)
            return offer

    # ---- reading ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            waiting = self.waiting()
            return {
                "waiting": waiting,
                "tickets": [asdict(t) for t in self._tickets.values()],
                "slots": [asdict(s) for s in self._slots.values()],
                "offers": [asdict(o) for o in self._offers.values()],
                "bookings": [asdict(b) for b in self._bookings_by_key.values()],
            }

    # ---- internals -------------------------------------------------------

    def _live_offer(self, offer_id: str, ticket: int) -> Offer:
        offer = self.offer(offer_id)
        if offer.ticket != ticket:
            raise NotYourOffer(f"offer {offer_id} was not made to ticket {ticket}")
        if offer.status in ("offered", "accepted") and offer.expires_at <= self._clock():
            self._release(offer, "expired", count_miss=True)
        if offer.status == "expired":
            raise LeaseExpired(f"the lease on {offer_id} ran out")
        return offer

    def _release(self, offer: Offer, new_status: str, count_miss: bool) -> None:
        """Take the slot back from this offer and make it offerable again."""
        offer.status = new_status
        slot = self._slots[offer.slot_id]
        slot.tried[offer.ticket] = None
        slot.status = "open"
        self._active_offer_by_ticket.pop(offer.ticket, None)
        if self._active_offer_by_slot.get(offer.slot_id) == offer.offer_id:
            self._active_offer_by_slot.pop(offer.slot_id, None)
        if count_miss:
            ticket = self._tickets[offer.ticket]
            ticket.misses += 1
            if ticket.misses >= self.max_misses and ticket.status == "waiting":
                ticket.status = "no_show"
