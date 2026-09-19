"""Persistence models.

Two tables:

- events: one row per logical event (unique on the caller-supplied
  `event_id`). This uniqueness constraint is the actual idempotency
  guarantee -- not an in-memory check -- so concurrent duplicate
  submissions cannot create two logical jobs even under a race.
- delivery_attempts: append-only log, one row per attempt. Never updated,
  only inserted, so it is always a faithful history.
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class DeliveryState(str, enum.Enum):
    """Explicit delivery states and their valid transitions.

    PENDING      -> IN_PROGRESS                 (scheduler claims it)
    IN_PROGRESS  -> DELIVERED                    (successful response)
    IN_PROGRESS  -> PENDING                      (retryable failure, attempts remain)
    IN_PROGRESS  -> FAILED                       (non-retryable failure, or attempts exhausted)
    IN_PROGRESS  -> IN_PROGRESS (re-claimed)     (claim went stale -- process likely crashed
                                                   mid-delivery; treated as due again so the
                                                   event is not lost)

    DELIVERED and FAILED are terminal: no further transitions occur.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    FAILED = "failed"


class AttemptOutcome(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class EventRecord(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_events_event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    state: Mapped[DeliveryState] = mapped_column(
        Enum(DeliveryState), nullable=False, default=DeliveryState.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    # Set when the scheduler claims this event for delivery (state -> IN_PROGRESS).
    # Used to detect and reclaim events left stuck IN_PROGRESS by a process
    # that crashed mid-delivery -- see repository.list_due_events.
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    attempts: Mapped[list["DeliveryAttempt"]] = relationship(
        back_populates="event",
        order_by="DeliveryAttempt.attempt_number",
        cascade="all, delete-orphan",
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_pk: Mapped[str] = mapped_column(String(36), ForeignKey("events.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    outcome: Mapped[AttemptOutcome] = mapped_column(Enum(AttemptOutcome), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    event: Mapped["EventRecord"] = relationship(back_populates="attempts")
