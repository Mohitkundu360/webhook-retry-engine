from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.config import RetrySettings
from app.db import make_engine, make_session_factory
from app.scheduler import DeliveryScheduler
from app.sender import WebhookSender
from tests.fake_receiver import ScriptedReceiver

BASE_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


@pytest.fixture()
def session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    engine = make_engine(db_url)
    return make_session_factory(engine)


@pytest.fixture()
def db_session(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture()
def retry_settings():
    # Small, fast-to-reason-about policy for tests. Real bounds live in
    # app/config.py; these are deliberately test-local.
    return RetrySettings(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=5.0)


def build_scheduler(script: list[int], retry_settings: RetrySettings) -> tuple[DeliveryScheduler, ScriptedReceiver]:
    """Wire a scheduler up to an in-process fake receiver (httpx.MockTransport).

    No real network call, no real listening socket, no real sleep.
    """
    receiver = ScriptedReceiver(script)
    sender = WebhookSender(receiver.client)
    scheduler = DeliveryScheduler(sender, "http://fake-receiver/receive", retry_settings)
    return scheduler, receiver
