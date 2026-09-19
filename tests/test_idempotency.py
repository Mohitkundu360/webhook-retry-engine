"""AC4: repeated submissions with the same eventId reference the existing
logical event and never create a second independent delivery job.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.models import EventRecord
from app.repository import create_event_if_new
from app.schemas import EventIn
from tests.conftest import BASE_TIME

SAMPLE_EVENT = EventIn(
    eventId="evt_dup",
    type="incident.created",
    occurredAt="2026-09-15T10:00:00Z",
    payload={"incidentId": "inc_456", "severity": "high"},
)


def test_repeated_submission_is_idempotent(db_session):
    first, created_first = create_event_if_new(db_session, SAMPLE_EVENT, now=BASE_TIME)
    second, created_second = create_event_if_new(db_session, SAMPLE_EVENT, now=BASE_TIME)

    assert created_first is True
    assert created_second is False
    assert first.id == second.id  # same logical row, not a new job

    count = db_session.execute(
        select(func.count()).select_from(EventRecord).where(EventRecord.event_id == "evt_dup")
    ).scalar_one()
    assert count == 1


def test_duplicate_insert_is_rejected_at_the_database_level(session_factory):
    """Idempotency must hold even if two requests race past an app-level
    check at the same instant -- so we bypass create_event_if_new's
    try/insert-first path is exactly what's under test here: committing a
    second row with the same event_id directly must fail on the unique
    constraint, proving the guarantee lives in the schema, not application
    logic that could be raced.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models import DeliveryState

    session_a = session_factory()
    session_b = session_factory()
    try:
        record_a = EventRecord(
            event_id="evt_race",
            event_type="incident.created",
            occurred_at="2026-09-15T10:00:00Z",
            payload={},
            state=DeliveryState.PENDING,
            attempt_count=0,
            next_attempt_at=BASE_TIME,
        )
        session_a.add(record_a)
        session_a.commit()

        record_b = EventRecord(
            event_id="evt_race",
            event_type="incident.created",
            occurred_at="2026-09-15T10:00:00Z",
            payload={},
            state=DeliveryState.PENDING,
            attempt_count=0,
            next_attempt_at=BASE_TIME,
        )
        session_b.add(record_b)
        try:
            session_b.commit()
            assert False, "expected IntegrityError on duplicate event_id"
        except IntegrityError:
            session_b.rollback()
    finally:
        session_a.close()
        session_b.close()
