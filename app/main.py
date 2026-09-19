"""FastAPI application.

Endpoints:
  POST /events         -- ingest an event (idempotent on eventId)
  GET  /events/{id}     -- current state + ordered attempt history
  GET  /events          -- list all events (demo/inspection convenience)

Ingestion just persists the event and leaves it PENDING; a background
thread polls for due deliveries and drives them through the scheduler.
Separating ingestion from delivery keeps the request path fast and means an
event already survives a crash the instant it's committed to the DB, before
any HTTP call to the receiver is ever made.
"""
from __future__ import annotations

import logging
import os
import threading

import httpx
from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy.orm import Session

from sqlalchemy import select

from app import repository
from app.config import AppSettings
from app.db import make_engine, make_session_factory
from app.models import EventRecord
from app.schemas import EventIn, EventOut, IngestResponse, event_to_schema
from app.scheduler import DeliveryScheduler, run_background_loop
from app.sender import WebhookSender

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = AppSettings.from_env()
engine = make_engine(settings.database_url)
SessionFactory = make_session_factory(engine)

http_client = httpx.Client(timeout=settings.request_timeout_seconds)
sender = WebhookSender(http_client)
scheduler = DeliveryScheduler(sender, settings.webhook_url, settings.retry, settings.stale_claim_seconds)

app = FastAPI(title="Webhook Retry Engine")

_stop_event = threading.Event()
_background_thread: threading.Thread | None = None


def get_session():
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


@app.on_event("startup")
def _start_background_worker() -> None:
    global _background_thread
    if os.environ.get("WEBHOOK_ENGINE_DISABLE_BACKGROUND") == "1":
        return  # tests drive the scheduler explicitly instead
    _stop_event.clear()
    _background_thread = threading.Thread(
        target=run_background_loop,
        args=(scheduler, SessionFactory, settings.poll_interval_seconds, _stop_event),
        daemon=True,
    )
    _background_thread.start()


@app.on_event("shutdown")
def _stop_background_worker() -> None:
    _stop_event.set()
    if _background_thread is not None:
        _background_thread.join(timeout=5)


@app.post("/events", response_model=IngestResponse)
def ingest_event(event_in: EventIn, response: Response, session: Session = Depends(get_session)) -> IngestResponse:
    record, created = repository.create_event_if_new(session, event_in)
    # 201 for a newly created logical event; 200 when this call referenced
    # an event that already existed (idempotent replay of the same eventId).
    response.status_code = 201 if created else 200
    return IngestResponse(event=event_to_schema(record), created=created)


@app.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: str, session: Session = Depends(get_session)) -> EventOut:
    record = repository.get_event_by_event_id(session, event_id)
    if record is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event_to_schema(record)


@app.get("/events", response_model=list[EventOut])
def list_events(session: Session = Depends(get_session)) -> list[EventOut]:
    records = session.execute(select(EventRecord).order_by(EventRecord.created_at)).scalars().all()
    return [event_to_schema(r) for r in records]
