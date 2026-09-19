# Webhook Retry Engine

Caygnus Product Engineering Challenge — Problem 2. Accepts an event, delivers it to one
configured webhook endpoint, retries bounded temporary failures, and keeps an inspectable,
ordered attempt history. Ingestion is idempotent on the caller-supplied `eventId`.

Full write-up (architecture, decisions, trade-offs, production considerations) is in
[`SUBMISSION.md`](./SUBMISSION.md). This README only covers running it.

## Setup

```bash
pip install -r requirements.txt
```

Python 3.11+. No external services or credentials required — persistence is a local
SQLite file, created on first run.

## Run it

Two processes: a receiver (something for the service to deliver to) and the service
itself. A demo receiver is included (`demo_receiver.py`) that fails its first two calls
then succeeds, so you can see retry behavior without standing up anything else.

```bash
# terminal 1
python3 -m uvicorn demo_receiver:app --port 9100

# terminal 2
export WEBHOOK_TARGET_URL="http://127.0.0.1:9100/receive"
export WEBHOOK_POLL_INTERVAL_SECONDS=0.3   # shortened for a fast local demo
export WEBHOOK_RETRY_BASE_SECONDS=0.5
python3 -m uvicorn app.main:app --port 9200
```

```bash
# submit an event
curl -X POST http://127.0.0.1:9200/events \
  -H "Content-Type: application/json" \
  -d '{"eventId":"evt_1","type":"incident.created","occurredAt":"2026-09-15T10:00:00Z","payload":{"incidentId":"inc_456","severity":"high"}}'

# inspect state + attempt history
curl http://127.0.0.1:9200/events/evt_1

# resubmit the same eventId -- idempotent, no new job
curl -X POST http://127.0.0.1:9200/events -H "Content-Type: application/json" \
  -d '{"eventId":"evt_1","type":"incident.created","occurredAt":"2026-09-15T10:00:00Z","payload":{"incidentId":"inc_456","severity":"high"}}'
```

All configuration is via environment variables (see `app/config.py` for the full list and
defaults) — nothing is hard-coded, and nothing secret needs to be set.

## Tests

```bash
python3 -m pytest -v
```

8 tests, fully deterministic (no real sleeps — see `SUBMISSION.md` for how).
