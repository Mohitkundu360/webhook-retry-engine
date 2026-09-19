"""Processes due deliveries.

`process_due` is the single unit of work: find events whose next attempt is
due, attempt delivery for each, and apply the retry policy to decide the
resulting state. It takes `now` as a parameter so tests can drive it with a
fake clock and call it directly -- no real sleeping required to exercise
retry-then-succeed or retry-until-exhausted behavior.

In the running service, `run_background_loop` calls `process_due` on a
timer using the real clock. That loop is the only place real time matters;
everything below it is pure and testable.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy.orm import Session

from app import repository
from app.config import RetrySettings
from app.models import AttemptOutcome, DeliveryState, EventRecord
from app.retry_policy import compute_backoff_seconds
from app.sender import WebhookSender

logger = logging.getLogger("webhook_engine.scheduler")


class DeliveryScheduler:
    def __init__(
        self,
        sender: WebhookSender,
        webhook_url: str,
        retry_settings: RetrySettings,
        stale_claim_seconds: float = 120.0,
    ):
        self._sender = sender
        self._webhook_url = webhook_url
        self._retry_settings = retry_settings
        self._stale_claim_seconds = stale_claim_seconds

    def process_due(self, session: Session, now: dt.datetime | None = None) -> int:
        """Attempt delivery for every event that is due. Returns count processed.

        A single event raising an unexpected error does not abort the batch
        or the caller: it is logged, the session is rolled back to a clean
        state, and processing continues with the next due event. Without
        this isolation, one bad delivery could kill the background thread
        and silently halt all future delivery.
        """
        now = now or dt.datetime.now(dt.timezone.utc)
        due = repository.list_due_events(session, now, self._stale_claim_seconds)
        processed = 0
        for event in due:
            try:
                self._process_one(session, event, now)
                processed += 1
            except Exception:  # noqa: BLE001 -- deliberate: isolate one bad event from the batch
                logger.exception("unexpected error processing event %s; will retry on next reclaim", event.event_id)
                session.rollback()
        return processed

    def _process_one(self, session: Session, event: EventRecord, now: dt.datetime) -> None:
        repository.claim_for_delivery(session, event, now)

        result = self._sender.send(
            url=self._webhook_url,
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=event.payload,
        )

        if result.success:
            repository.record_attempt(
                session, event, AttemptOutcome.SUCCESS, result.http_status, result.error_detail
            )
            repository.mark_delivered(session, event)
            logger.info("event %s delivered on attempt %d", event.event_id, event.attempt_count)
            return

        attempt = repository.record_attempt(
            session, event, AttemptOutcome.FAILURE, result.http_status, result.error_detail
        )

        attempts_remaining = attempt.attempt_number < self._retry_settings.max_attempts
        if result.retryable and attempts_remaining:
            delay = compute_backoff_seconds(
                attempt.attempt_number,
                self._retry_settings.base_delay_seconds,
                self._retry_settings.max_delay_seconds,
            )
            next_attempt_at = now + dt.timedelta(seconds=delay)
            repository.schedule_retry(session, event, next_attempt_at)
            logger.info(
                "event %s attempt %d failed (retryable), next attempt at %s",
                event.event_id,
                attempt.attempt_number,
                next_attempt_at,
            )
        else:
            repository.mark_failed(session, event)
            reason = "attempts exhausted" if result.retryable else "non-retryable failure"
            logger.info(
                "event %s attempt %d failed terminally (%s)",
                event.event_id,
                attempt.attempt_number,
                reason,
            )


def run_background_loop(
    scheduler: DeliveryScheduler,
    session_factory,
    poll_interval_seconds: float,
    stop_event,
) -> None:
    """Real-time polling loop. Runs in a background thread; not exercised by tests."""
    while not stop_event.is_set():
        session = session_factory()
        try:
            scheduler.process_due(session)
        except Exception:  # noqa: BLE001 -- keep the loop alive across unexpected failures (e.g. DB hiccup)
            logger.exception("background delivery loop iteration failed")
        finally:
            session.close()
        stop_event.wait(poll_interval_seconds)
