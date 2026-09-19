"""Application configuration.

Settings are plain, explicit dataclasses rather than a global singleton so
tests can construct their own instances (e.g. tiny backoff, low attempt
limits) without mutating process-wide state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrySettings:
    """Bounded retry policy.

    max_attempts: total number of delivery attempts allowed for one event
        (the first attempt counts as attempt 1). Once this many attempts
        have been made without success, the event moves to a terminal
        FAILED state and is never retried again.
    base_delay_seconds / max_delay_seconds: exponential backoff bounds used
        by `compute_backoff_seconds` in retry_policy.py.
    """

    max_attempts: int = 5
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "RetrySettings":
        return cls(
            max_attempts=int(os.environ.get("WEBHOOK_RETRY_MAX_ATTEMPTS", 5)),
            base_delay_seconds=float(os.environ.get("WEBHOOK_RETRY_BASE_SECONDS", 2.0)),
            max_delay_seconds=float(os.environ.get("WEBHOOK_RETRY_MAX_SECONDS", 60.0)),
        )


@dataclass(frozen=True)
class AppSettings:
    webhook_url: str
    database_url: str
    poll_interval_seconds: float
    request_timeout_seconds: float
    stale_claim_seconds: float
    retry: RetrySettings

    @classmethod
    def from_env(cls) -> "AppSettings":
        return cls(
            webhook_url=os.environ.get(
                "WEBHOOK_TARGET_URL", "http://localhost:9000/receive"
            ),
            database_url=os.environ.get(
                "DATABASE_URL", "sqlite:///./webhook_engine.db"
            ),
            poll_interval_seconds=float(os.environ.get("WEBHOOK_POLL_INTERVAL_SECONDS", 1.0)),
            request_timeout_seconds=float(os.environ.get("WEBHOOK_REQUEST_TIMEOUT_SECONDS", 5.0)),
            stale_claim_seconds=float(os.environ.get("WEBHOOK_STALE_CLAIM_SECONDS", 120.0)),
            retry=RetrySettings.from_env(),
        )
