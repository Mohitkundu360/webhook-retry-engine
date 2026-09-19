"""Phase 5 hardening: crash recovery for stuck IN_PROGRESS events, and
exception isolation so one bad delivery does not halt the whole batch.
"""
from __future__ import annotations

import datetime as dt

from app.models import DeliveryState
from app.repository import claim_for_delivery, create_event_if_new, get_event_by_event_id
from app.schemas import EventIn
from tests.conftest import BASE_TIME, build_scheduler

SAMPLE_EVENT = EventIn(
    eventId="evt_crash",
    type="incident.created",
    occurredAt="2026-09-15T10:00:00Z",
    payload={},
)


def test_stale_in_progress_event_is_reclaimed_and_completed(db_session, retry_settings):
    """Simulates a crash: an event was claimed (IN_PROGRESS) but the process
    died before any attempt was recorded or the state moved on. Once the
    claim is older than the stale window, the scheduler must pick it back
    up rather than leaving it stuck forever.
    """
    scheduler, receiver = build_scheduler(script=[200], retry_settings=retry_settings)
    event, _ = create_event_if_new(db_session, SAMPLE_EVENT, now=BASE_TIME)

    # Simulate: scheduler claimed it, then the process crashed before doing
    # anything else -- no attempt row, state stuck IN_PROGRESS.
    claim_for_delivery(db_session, event, now=BASE_TIME)
    stuck = get_event_by_event_id(db_session, "evt_crash")
    assert stuck.state == DeliveryState.IN_PROGRESS

    # Immediately after the crash, still within the stale window: not reclaimed.
    processed = scheduler.process_due(db_session, now=BASE_TIME + dt.timedelta(seconds=10))
    assert processed == 0
    assert receiver.call_count == 0

    # Past the stale window: reclaimed, delivered, recorded.
    long_after = BASE_TIME + dt.timedelta(seconds=200)  # default stale window is 120s
    processed = scheduler.process_due(db_session, now=long_after)
    assert processed == 1

    recovered = get_event_by_event_id(db_session, "evt_crash")
    assert recovered.state == DeliveryState.DELIVERED
    assert recovered.attempt_count == 1
    assert recovered.claimed_at is None


def test_one_failing_event_does_not_block_others_in_the_same_batch(db_session, retry_settings):
    """A raised exception while processing one due event must not prevent
    other due events in the same process_due() call from being handled.
    """
    scheduler, receiver = build_scheduler(script=[200], retry_settings=retry_settings)
    good_event = EventIn(eventId="evt_good", type="incident.created", occurredAt="2026-09-15T10:00:00Z", payload={})
    create_event_if_new(db_session, good_event, now=BASE_TIME)

    # Force _process_one to raise for a specific event by handing the
    # scheduler a sender that blows up only for eventId "evt_bad".
    bad_event = EventIn(eventId="evt_bad", type="incident.created", occurredAt="2026-09-15T10:00:00Z", payload={})
    create_event_if_new(db_session, bad_event, now=BASE_TIME)

    original_send = scheduler._sender.send

    def flaky_send(*, event_id, **kwargs):
        if event_id == "evt_bad":
            raise RuntimeError("boom")
        return original_send(event_id=event_id, **kwargs)

    scheduler._sender.send = flaky_send

    processed = scheduler.process_due(db_session, now=BASE_TIME)

    good = get_event_by_event_id(db_session, "evt_good")
    bad = get_event_by_event_id(db_session, "evt_bad")

    assert good.state == DeliveryState.DELIVERED
    # The bad event's state may vary depending on iteration order, but it
    # must not have crashed the batch and must not be silently lost --
    # it stays claimable (IN_PROGRESS, to be reclaimed) or PENDING.
    assert bad.state in (DeliveryState.IN_PROGRESS, DeliveryState.PENDING)
    assert processed == 1  # only the good event completed cleanly
