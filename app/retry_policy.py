"""Retry classification and backoff.

Documented policy (see SUBMISSION.md for the reviewer-facing version):

Successful HTTP responses: 2xx. Anything else is a failed attempt.

Retryable (temporary) failures:
  - HTTP 429 (rate limited)
  - HTTP 5xx (server error)
  - Connection errors, timeouts, and other transport-level failures
    (the receiver could not be reached at all)

Terminal (non-retryable) failures:
  - HTTP 4xx other than 429 (the request itself is rejected -- retrying an
    identical payload will not change the outcome)

We do not blindly retry every error: a 400/404/422 etc. means the receiver
understood and rejected the request, so retrying wastes attempts and delays
surfacing a real integration bug.
"""
from __future__ import annotations


def is_success_status(status_code: int) -> bool:
    return 200 <= status_code < 300


def is_retryable_status(status_code: int) -> bool:
    if is_success_status(status_code):
        return False
    if status_code == 429:
        return True
    if 500 <= status_code < 600:
        return True
    return False  # other 4xx: terminal


def compute_backoff_seconds(attempt_number: int, base: float, cap: float) -> float:
    """Exponential backoff: base * 2^(attempt_number - 1), capped.

    attempt_number is the attempt that just failed (1-indexed), so the delay
    before attempt 2 is `base`, before attempt 3 is `2*base`, etc.
    """
    delay = base * (2 ** (attempt_number - 1))
    return min(delay, cap)
