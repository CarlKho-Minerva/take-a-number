"""The rules of the line, tested with a clock we move by hand."""

import pytest

from take_a_number.queue import (
    IdempotencyConflict,
    LeaseExpired,
    NotYourOffer,
    TicketLine,
    UnknownOffer,
    UnknownTicket,
    WrongState,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def line(clock):
    line = TicketLine(clock=clock, first_number=41)
    for holder in ("dev", "ana", "sam"):
        line.take_ticket(holder)
    return line


# ---- queue order --------------------------------------------------------


def test_tickets_are_numbered_in_arrival_order(line):
    assert line.waiting() == [41, 42, 43]
    assert [line.position(n) for n in (41, 42, 43)] == [1, 2, 3]


def test_one_ticket_per_holder(line):
    again = line.take_ticket("ana")
    assert again.number == 42
    assert line.waiting() == [41, 42, 43]


def test_unknown_ticket_has_no_position(line):
    with pytest.raises(UnknownTicket):
        line.position(7)


def test_offer_goes_to_the_head_of_the_line(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", lease_seconds=60)
    assert offer.ticket == 41
    assert offer.expires_at - offer.issued_at == 60


def test_slot_is_exclusive_while_leased(line):
    line.open_slot("s1")
    assert line.next_offer("s1", 60).ticket == 41
    assert line.next_offer("s1", 60) is None  # nobody else is asked meanwhile


def test_empty_line_leaves_slot_unfilled(clock):
    line = TicketLine(clock=clock)
    line.open_slot("s1")
    assert line.next_offer("s1", 60) is None
    assert line.slot("s1").status == "unfilled"


def test_position_moves_up_after_someone_is_served(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    line.accept(offer.offer_id, 41)
    line.confirm(offer.offer_id, 41, "k1")
    assert line.position(41) is None
    assert line.position(42) == 1
    assert line.position(43) == 2


# ---- pass ---------------------------------------------------------------


def test_pass_sends_the_slot_to_the_next_ticket(line):
    line.open_slot("s1")
    first = line.next_offer("s1", 60)
    line.pass_(first.offer_id, 41)
    second = line.next_offer("s1", 60)
    assert second.ticket == 42


def test_pass_keeps_your_place_for_future_slots(line):
    line.open_slot("s1")
    first = line.next_offer("s1", 60)
    line.pass_(first.offer_id, 41)
    assert line.position(41) == 1  # still at the head
    assert line.ticket(41).misses == 0  # a pass is not a penalty
    second = line.next_offer("s1", 60)
    line.accept(second.offer_id, 42)
    line.confirm(second.offer_id, 42, "k1")
    line.open_slot("s2")
    assert line.next_offer("s2", 60).ticket == 41  # first in line for the next slot


def test_passer_is_not_asked_twice_about_the_same_slot(line):
    line.open_slot("s1")
    for expected in (41, 42, 43):
        offer = line.next_offer("s1", 60)
        assert offer.ticket == expected
        line.pass_(offer.offer_id, expected)
    assert line.next_offer("s1", 60) is None
    assert line.slot("s1").status == "unfilled"
    assert line.waiting() == [41, 42, 43]


def test_only_the_offered_ticket_can_answer(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    with pytest.raises(NotYourOffer):
        line.accept(offer.offer_id, 42)
    with pytest.raises(NotYourOffer):
        line.pass_(offer.offer_id, 43)
    with pytest.raises(UnknownOffer):
        line.accept("offer-999", 41)


# ---- lease expiry -------------------------------------------------------


def test_lease_expiry_releases_the_slot_to_the_next_ticket(line, clock):
    line.open_slot("s1")
    first = line.next_offer("s1", 60)
    clock.tick(59.9)
    assert line.expire_due() == []
    clock.tick(0.1)
    expired = line.expire_due()
    assert [o.offer_id for o in expired] == [first.offer_id]
    assert line.next_offer("s1", 60).ticket == 42


def test_accept_after_the_lease_ran_out_is_refused(line, clock):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    clock.tick(61)
    with pytest.raises(LeaseExpired):
        line.accept(offer.offer_id, 41)
    assert line.next_offer("s1", 60).ticket == 42


def test_lease_expiring_between_accept_and_confirm_does_not_book(line, clock):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    line.accept(offer.offer_id, 41)
    clock.tick(61)
    with pytest.raises(LeaseExpired):
        line.confirm(offer.offer_id, 41, "k1")
    assert line.slot("s1").status == "open"
    assert line.slot("s1").booked_by is None
    # and the slot is still bookable by the next ticket
    nxt = line.next_offer("s1", 60)
    line.accept(nxt.offer_id, 42)
    booking, replayed = line.confirm(nxt.offer_id, 42, "k2")
    assert (booking.ticket, replayed) == (42, False)


def test_a_miss_keeps_your_place_but_two_misses_is_a_no_show(line, clock):
    line.open_slot("s1")
    line.next_offer("s1", 60)
    clock.tick(61)
    line.expire_due()
    assert line.ticket(41).misses == 1
    assert line.position(41) == 1  # one miss: still in line, still first

    line.open_slot("s2")
    assert line.next_offer("s2", 60).ticket == 41
    clock.tick(61)
    line.expire_due()
    assert line.ticket(41).status == "no_show"
    assert line.waiting() == [42, 43]
    assert line.position(41) is None


def test_undeliverable_offer_counts_as_a_miss_and_moves_on(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    line.fail_delivery(offer.offer_id)
    assert line.ticket(41).misses == 1
    assert line.next_offer("s1", 60).ticket == 42


def test_expire_due_is_safe_to_call_repeatedly(line, clock):
    line.open_slot("s1")
    line.next_offer("s1", 60)
    clock.tick(61)
    assert len(line.expire_due()) == 1
    assert line.expire_due() == []
    assert line.ticket(41).misses == 1


# ---- idempotent confirm ---------------------------------------------------


def test_confirm_retry_returns_the_same_booking(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    line.accept(offer.offer_id, 41)
    first, replayed_first = line.confirm(offer.offer_id, 41, "key-abc")
    second, replayed_second = line.confirm(offer.offer_id, 41, "key-abc")
    assert replayed_first is False
    assert replayed_second is True
    assert first.booking_id == second.booking_id
    assert len(line.snapshot()["bookings"]) == 1


def test_confirm_retry_still_works_after_the_lease_time_has_passed(line, clock):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    line.accept(offer.offer_id, 41)
    first, _ = line.confirm(offer.offer_id, 41, "key-abc")
    clock.tick(3600)
    second, replayed = line.confirm(offer.offer_id, 41, "key-abc")
    assert replayed is True and second.booking_id == first.booking_id


def test_same_key_on_a_different_offer_is_refused(line):
    line.open_slot("s1")
    line.open_slot("s2")
    o1 = line.next_offer("s1", 60)
    line.accept(o1.offer_id, 41)
    line.confirm(o1.offer_id, 41, "key-abc")
    o2 = line.next_offer("s2", 60)
    line.accept(o2.offer_id, 42)
    with pytest.raises(IdempotencyConflict):
        line.confirm(o2.offer_id, 42, "key-abc")
    assert line.slot("s2").status == "offered"  # nothing was booked


def test_second_key_on_a_confirmed_offer_does_not_double_book(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    line.accept(offer.offer_id, 41)
    line.confirm(offer.offer_id, 41, "key-1")
    with pytest.raises(IdempotencyConflict):
        line.confirm(offer.offer_id, 41, "key-2")
    assert len(line.snapshot()["bookings"]) == 1


def test_confirm_without_accept_is_refused(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    with pytest.raises(WrongState):
        line.confirm(offer.offer_id, 41, "k1")
    with pytest.raises(WrongState):
        line.confirm(offer.offer_id, 41, "")


def test_repeated_accept_is_harmless(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    assert line.accept(offer.offer_id, 41).status == "accepted"
    assert line.accept(offer.offer_id, 41).status == "accepted"


# ---- two slots at once ----------------------------------------------------


def test_a_ticket_holds_one_offer_at_a_time(line):
    line.open_slot("s1")
    line.open_slot("s2")
    assert line.next_offer("s1", 60).ticket == 41
    assert line.next_offer("s2", 60).ticket == 42  # 41 is busy, so 42 is asked
    assert line.next_offer("s1", 60) is None


def test_booked_slot_is_never_offered_again(line):
    line.open_slot("s1")
    offer = line.next_offer("s1", 60)
    line.accept(offer.offer_id, 41)
    line.confirm(offer.offer_id, 41, "k1")
    line.open_slot("s1")  # opening again is a no-op
    assert line.next_offer("s1", 60) is None
    assert line.slot("s1").booked_by == 41


def test_a_busy_ticket_is_asked_later_not_skipped_for_good(line):
    line.open_slot("s1")
    line.open_slot("s2")
    first = line.next_offer("s1", 60)  # 41 is now busy with s1
    for expected in (42, 43):
        offer = line.next_offer("s2", 60)
        assert offer.ticket == expected
        line.pass_(offer.offer_id, expected)
    assert line.next_offer("s2", 60) is None  # only 41 is left and 41 is busy
    assert line.slot("s2").status == "open"  # so the slot waits; it is not written off
    assert line.open_slots() == ["s2"]
    line.pass_(first.offer_id, 41)
    assert line.next_offer("s2", 60).ticket == 41


def test_late_joiner_is_offered_a_slot_everyone_else_passed(line):
    line.open_slot("s1")
    for expected in (41, 42, 43):
        offer = line.next_offer("s1", 60)
        line.pass_(offer.offer_id, expected)
    assert line.next_offer("s1", 60) is None and line.slot("s1").status == "unfilled"
    newcomer = line.take_ticket("zoe")
    line.open_slot("s1")  # the desk puts the still-free slot back up
    assert line.next_offer("s1", 60).ticket == newcomer.number


def test_everyone_passing_stays_fast_with_a_long_line(clock):
    import time as real_time

    line = TicketLine(clock=clock)
    for i in range(3000):
        line.take_ticket(f"agent-{i}")
    line.open_slot("s1")
    started = real_time.perf_counter()
    while (offer := line.next_offer("s1", 60)) is not None:
        line.pass_(offer.offer_id, offer.ticket)
    assert real_time.perf_counter() - started < 1.0  # was 3.5 s at 2,000 tickets before the cursor
    assert len(line.waiting()) == 3000
