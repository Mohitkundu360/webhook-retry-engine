"""A tiny controllable webhook receiver used for tests and the demo.

`ScriptedReceiver` returns a scripted sequence of HTTP statuses on each
call, e.g. [500, 500, 200] to simulate two temporary failures followed by
success. It records every request body it receives so tests can assert
exactly what was delivered.

Implemented as an httpx.MockTransport handler -- a fake transport, per the
assignment's "local receivers, fake transports, or equivalent" -- so
delivery goes through the real WebhookSender / httpx.Client code path with
no real socket, no real network, and no real sleeping.
"""
from __future__ import annotations

import json

import httpx


class ScriptedReceiver:
    def __init__(self, script: list[int] | None = None):
        # each call returns the next status in `script`; once exhausted,
        # repeats the last entry.
        self._script = script or [200]
        self._call_count = 0
        self.received_bodies: list[dict] = []

        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.received_bodies.append(json.loads(request.content))
        status = self._next_status()
        return httpx.Response(status_code=status, text="ok" if status < 300 else "error")

    def _next_status(self) -> int:
        idx = min(self._call_count, len(self._script) - 1)
        self._call_count += 1
        return self._script[idx]

    @property
    def call_count(self) -> int:
        return self._call_count
