from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    eventId: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    occurredAt: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AttemptOut(BaseModel):
    attemptNumber: int
    attemptedAt: dt.datetime
    outcome: str
    httpStatus: int | None
    errorDetail: str | None

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    eventId: str
    type: str
    occurredAt: str
    payload: dict[str, Any]
    state: str
    attemptCount: int
    createdAt: dt.datetime
    updatedAt: dt.datetime
    attempts: list[AttemptOut]

    model_config = {"from_attributes": True}


class IngestResponse(BaseModel):
    event: EventOut
    created: bool  # False when this call referenced an already-existing event


def attempt_to_schema(attempt) -> AttemptOut:
    return AttemptOut(
        attemptNumber=attempt.attempt_number,
        attemptedAt=attempt.attempted_at,
        outcome=attempt.outcome.value,
        httpStatus=attempt.http_status,
        errorDetail=attempt.error_detail,
    )


def event_to_schema(record) -> EventOut:
    return EventOut(
        eventId=record.event_id,
        type=record.event_type,
        occurredAt=record.occurred_at,
        payload=record.payload,
        state=record.state.value,
        attemptCount=record.attempt_count,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
        attempts=[attempt_to_schema(a) for a in record.attempts],
    )
