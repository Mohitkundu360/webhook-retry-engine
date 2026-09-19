"""AC1, AC2, AC3.

Each test drives DeliveryScheduler.process_due directly with an explicit,
test-controlled `now`. No real sleeping, no timing flakiness: retry
eligibility is decided purely by comparing `next_attempt_at` to the `now`
we pass in.
"""
from __future__ import annotations

import datetime as dt

from app.models import AttemptOutcome, DeliveryState
from app.repository import create_event_if_new, get_event_by_event_id
from app.schemas import EventIn
from tests.conftest import BASE_TIME, build_scheduler

SAMPLE_EVENT = EventIn(
    eventId="evt_123",
    type="incident.created",
    occurredAt="2026-09-15T10:00:00Z",
    payload={"incidentId": "inc_456", "severity": "high"},
)


def test_successful_delivery_is_recorded_and_marked_delivered(db_session, retry_settings):
    """AC1: reachable receiver returns success -> delivered, one recorded attempt."""
    scheduler, receiver = build_scheduler(script=[200], retry_settings=retry_settings)
    create_event_if_new(db_session, SAMPLE_EVENT, now=BASE_TIME)

    processed = scheduler.process_due(db_session, now=BASE_TIME)

    assert processed == 1
    event = get_event_by_event_id(db_session, "evt_123")
    assert event.state == DeliveryState.DELIVERED
    assert event.attempt_count == 1
    assert len(event.attempts) == 1
    assert event.attempts[0].outcome == AttemptOutcome.SUCCESS
    assert event.attempts[0].http_status == 200
    assert receiver.received_bodies[0]["eventId"] == "evt_123"


def test_temporary_failure_then_retry_eventually_succeeds(db_session, retry_settings):
    """AC2: first attempt fails with a retryable error, second attempt (once due) succeeds."""
    scheduler, receiver = build_scheduler(script=[503, 200], retry_settings=retry_settings)
    create_event_if_new(db_session, SAMPLE_EVENT, now=BASE_TIME)

    # First pass: attempt 1 fails, event scheduled for retry (not yet due).
    scheduler.process_due(db_session, now=BASE_TIME)
    event = get_event_by_event_id(db_session, "evt_123")
    assert event.state == DeliveryState.PENDING
    assert event.attempt_count == 1
    assert event.attempts[0].outcome == AttemptOutcome.FAILURE
    assert event.attempts[0].http_status == 503

    # Not yet due: calling again with the same `now` must not re-attempt.
    processed = scheduler.process_due(db_session, now=BASE_TIME)
    assert processed == 0
    assert receiver.call_count == 1

    # Advance past the scheduled retry time -> second attempt fires and succeeds.
    later = event.next_attempt_at + dt.timedelta(seconds=1)
    processed = scheduler.process_due(db_session, now=later)
    assert processed == 1

    event = get_event_by_event_id(db_session, "evt_123")
    assert event.state == DeliveryState.DELIVERED
    assert event.attempt_count == 2
    outcomes = [a.outcome for a in event.attempts]
    assert outcomes == [AttemptOutcome.FAILURE, AttemptOutcome.SUCCESS]


def test_attempt_exhaustion_stops_retrying_and_marks_failed(db_session, retry_settings):
    """AC3: receiver fails continuously -> stops at max_attempts, terminal FAILED, no further attempts."""
    scheduler, receiver = build_scheduler(script=[500], retry_settings=retry_settings)  # always fails
    create_event_if_new(db_session, SAMPLE_EVENT, now=BASE_TIME)

    now = BASE_TIME
    for _ in range(retry_settings.max_attempts):
        event = get_event_by_event_id(db_session, "evt_123")
        scheduler.process_due(db_session, now=now)
        event = get_event_by_event_id(db_session, "evt_123")
        if event.state == DeliveryState.FAILED:
            break
        now = event.next_attempt_at + dt.timedelta(seconds=1)

    event = get_event_by_event_id(db_session, "evt_123")
    assert event.state == DeliveryState.FAILED
    assert event.attempt_count == retry_settings.max_attempts
    assert all(a.outcome == AttemptOutcome.FAILURE for a in event.attempts)

    # Attempts do not continue forever: event is no longer PENDING, so it is
    # never selected as "due" again, no matter how far time moves forward.
    calls_before = receiver.call_count
    far_future = now + dt.timedelta(days=365)
    processed = scheduler.process_due(db_session, now=far_future)
    assert processed == 0
    assert receiver.call_count == calls_before


def test_non_retryable_status_fails_immediately_without_retry(db_session, retry_settings):
    """A 4xx (other than 429) is terminal on the first attempt -- no retry burned on a bad request."""
    scheduler, receiver = build_scheduler(script=[400], retry_settings=retry_settings)
    create_event_if_new(db_session, SAMPLE_EVENT, now=BASE_TIME)

    scheduler.process_due(db_session, now=BASE_TIME)

    event = get_event_by_event_id(db_session, "evt_123")
    assert event.state == DeliveryState.FAILED
    assert event.attempt_count == 1
    assert event.attempts[0].http_status == 400
