"""Data access layer.

Idempotency is enforced by the database's unique constraint on
`events.event_id` (see models.py), not by an in-memory check-then-insert.
`create_event_if_new` attempts the insert directly and falls back to a
lookup on IntegrityError, so two concurrent requests for the same eventId
race safely: exactly one insert wins, the other observes the conflict and
returns the row the winner created. Neither request can create a second
logical delivery job.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AttemptOutcome, DeliveryState, DeliveryAttempt, EventRecord
from app.schemas import EventIn


def create_event_if_new(
    session: Session, event_in: EventIn, now: dt.datetime | None = None
) -> tuple[EventRecord, bool]:
    """Insert a new event, or return the existing one. Returns (record, created).

    `now` sets the initial `next_attempt_at` (defaults to the real clock).
    Tests pass an explicit value so the very first delivery attempt is
    immediately "due" relative to whatever fake clock the test then uses
    with the scheduler -- no dependency on wall-clock timing.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    record = EventRecord(
        event_id=event_in.eventId,
        event_type=event_in.type,
        occurred_at=event_in.occurredAt,
        payload=event_in.payload,
        state=DeliveryState.PENDING,
        attempt_count=0,
        next_attempt_at=now,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError:
        # Another request (or an earlier call with the same eventId) already
        # created this event. Roll back our attempted insert and fetch the
        # existing logical event instead of creating a second job.
        session.rollback()
        existing = get_event_by_event_id(session, event_in.eventId)
        assert existing is not None
        return existing, False
    session.refresh(record)
    return record, True


def get_event_by_event_id(session: Session, event_id: str) -> EventRecord | None:
    stmt = select(EventRecord).where(EventRecord.event_id == event_id)
    return session.execute(stmt).scalar_one_or_none()


def list_due_events(session: Session, now: dt.datetime, stale_claim_seconds: float = 120.0) -> list[EventRecord]:
    """Events ready for a delivery attempt: PENDING ones whose retry delay has
    elapsed, plus IN_PROGRESS ones whose claim is stale (older than
    `stale_claim_seconds`), which most likely means the process that claimed
    them crashed before recording an outcome. Reclaiming them is what makes
    the event recoverable instead of stuck forever -- see EventRecord.claimed_at.
    """
    stale_before = now - dt.timedelta(seconds=stale_claim_seconds)
    stmt = select(EventRecord).where(
        (
            (EventRecord.state == DeliveryState.PENDING)
            & (EventRecord.next_attempt_at <= now)
        )
        | (
            (EventRecord.state == DeliveryState.IN_PROGRESS)
            & (EventRecord.claimed_at.is_not(None))
            & (EventRecord.claimed_at <= stale_before)
        )
    )
    return list(session.execute(stmt).scalars())


def claim_for_delivery(session: Session, event: EventRecord, now: dt.datetime) -> None:
    event.state = DeliveryState.IN_PROGRESS
    event.claimed_at = now
    session.commit()


def record_attempt(
    session: Session,
    event: EventRecord,
    outcome: AttemptOutcome,
    http_status: int | None,
    error_detail: str | None,
) -> DeliveryAttempt:
    event.attempt_count += 1
    attempt = DeliveryAttempt(
        event_pk=event.id,
        attempt_number=event.attempt_count,
        outcome=outcome,
        http_status=http_status,
        error_detail=error_detail,
    )
    session.add(attempt)
    session.commit()
    return attempt


def mark_delivered(session: Session, event: EventRecord) -> None:
    event.state = DeliveryState.DELIVERED
    event.claimed_at = None
    session.commit()


def mark_failed(session: Session, event: EventRecord) -> None:
    event.state = DeliveryState.FAILED
    event.claimed_at = None
    session.commit()


def schedule_retry(session: Session, event: EventRecord, next_attempt_at: dt.datetime) -> None:
    event.state = DeliveryState.PENDING
    event.claimed_at = None
    event.next_attempt_at = next_attempt_at
    session.commit()
