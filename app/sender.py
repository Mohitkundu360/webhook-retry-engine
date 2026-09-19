"""Delivers a single event payload to the configured webhook endpoint.

The httpx transport is injectable so tests can point delivery at an
in-process ASGI receiver (via httpx.ASGITransport) or a fake transport that
raises connection errors on demand -- no real network, no real sleeping.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.retry_policy import is_retryable_status, is_success_status


@dataclass
class SendResult:
    success: bool
    retryable: bool
    http_status: int | None
    error_detail: str | None


class WebhookSender:
    def __init__(self, client: httpx.Client):
        self._client = client

    def send(self, url: str, event_id: str, event_type: str, occurred_at: str, payload: dict) -> SendResult:
        body = {
            "eventId": event_id,
            "type": event_type,
            "occurredAt": occurred_at,
            "payload": payload,
        }
        try:
            response = self._client.post(url, json=body)
        except httpx.TransportError as exc:
            # Could not reach the receiver at all (DNS, connect, timeout, ...).
            # Treated as a temporary failure.
            return SendResult(
                success=False,
                retryable=True,
                http_status=None,
                error_detail=f"{type(exc).__name__}: {exc}",
            )

        if is_success_status(response.status_code):
            return SendResult(success=True, retryable=False, http_status=response.status_code, error_detail=None)

        return SendResult(
            success=False,
            retryable=is_retryable_status(response.status_code),
            http_status=response.status_code,
            error_detail=response.text[:1024] if response.text else None,
        )
