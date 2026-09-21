# Product Engineering Challenge Submission

## Candidate

- **Name:** Mohit Kundu
- **Email:** mohitkundu2003@gmail.com
- **GitHub:** https://github.com/Mohitkundu360/webhook-retry-engine
- **Selected problem:** Problem 2 — Webhook Retry Engine
- **Demo video:** https://www.loom.com/share/039d7f5412594e9aa2faefae34f61806
- **Live demo:** https://webhook-retry-engine.onrender.com (receiver: https://webhook-demo-receiver.onrender.com)

## Run the project

Prerequisites: Python 3.11+, pip.

```bash
git clone <your-fork-url>
cd webhook-retry-engine
pip install -r requirements.txt

# Terminal 1: a local receiver you control (fails twice, then succeeds — for the retry demo)
python3 -m uvicorn demo_receiver:app --port 9100

# Terminal 2: the service itself
export WEBHOOK_TARGET_URL="http://127.0.0.1:9100/receive"
export WEBHOOK_POLL_INTERVAL_SECONDS=0.3   # shortened for demo purposes, see note below
export WEBHOOK_RETRY_BASE_SECONDS=0.5
python3 -m uvicorn app.main:app --port 9200
```

No secrets or credentials are required or committed. `DATABASE_URL` defaults to a local
SQLite file (`webhook_engine.db`, created on first run, git-ignored).

### Trigger the successful scenario

```bash
curl -X POST http://127.0.0.1:9200/events \
  -H "Content-Type: application/json" \
  -d '{"eventId":"evt_1","type":"incident.created","occurredAt":"2026-09-15T10:00:00Z","payload":{"incidentId":"inc_456","severity":"high"}}'

curl http://127.0.0.1:9200/events/evt_1
```

### Trigger the required failure/recovery scenario

The bundled `demo_receiver.py` fails its first two calls (HTTP 503) then succeeds, so any
event submitted against it exercises AC2 automatically — submit, wait ~2 seconds, then
`GET /events/{eventId}` and see two `failure` attempts followed by one `success`.

### Trigger bounded failure (AC3)

Point `WEBHOOK_TARGET_URL` at a receiver that always fails (or stop the receiver process
entirely — connection errors are retryable too). After `WEBHOOK_RETRY_MAX_ATTEMPTS`
attempts (default 5), the event's state becomes `failed` and stays there; no further
attempts are made no matter how long you wait.

### Idempotency

Re-POST the same body (same `eventId`) — the response has `"created": false` and HTTP
status `200` (vs `201` for a new event), referencing the same job and attempt history.

## Run the tests

```bash
pip install -r requirements.txt
python3 -m pytest -v
```

8 tests, all deterministic (no real sleeps — retry timing is driven by an explicit `now`
parameter passed into the scheduler, not the wall clock):

- `test_delivery.py` — AC1 (success), AC2 (temporary failure then retry succeeds), AC3
  (attempt exhaustion), plus a non-retryable-4xx-fails-immediately case
- `test_idempotency.py` — AC4 (repeated ingestion), plus a direct proof that the
  uniqueness guarantee is enforced by the database, not an application-level check
- `test_resilience.py` — crash recovery (a stuck `IN_PROGRESS` event is reclaimed), and
  exception isolation (one failing event in a batch doesn't block the others)

## Architecture and data flow

```
POST /events ──▶ Ingestion (FastAPI route)
                     │
                     ▼
              repository.create_event_if_new
              (INSERT; on unique-constraint conflict, fetch existing row instead)
                     │
                     ▼
                events table (state=PENDING)
                     │
     ┌───────────────┴────────────────┐
     │   background thread, polling    │
     │   every WEBHOOK_POLL_INTERVAL   │
     ▼                                 │
DeliveryScheduler.process_due ─────────┘
     │
     ├─▶ repository.list_due_events   (PENDING due, or IN_PROGRESS with a stale claim)
     ├─▶ repository.claim_for_delivery (state -> IN_PROGRESS, claimed_at = now)
     ├─▶ WebhookSender.send            (HTTP POST via httpx; injectable transport in tests)
     ├─▶ retry_policy.is_retryable_status / is_success_status  (classify the outcome)
     ├─▶ repository.record_attempt     (append-only delivery_attempts row)
     └─▶ repository.mark_delivered / schedule_retry / mark_failed

GET /events/{eventId} ──▶ repository.get_event_by_event_id ──▶ state + ordered attempts
```

Components and responsibilities:

- **app/main.py** — FastAPI routes and process wiring only; no business logic.
- **app/repository.py** — all database access. Idempotency and state transitions live
  here, not scattered across routes.
- **app/sender.py** — the actual HTTP call to the webhook endpoint. Takes an `httpx.Client`
  by constructor injection so tests substitute a fake transport (`httpx.MockTransport`)
  instead of hitting the network.
- **app/retry_policy.py** — pure functions: given a status code or exception, is it
  retryable? Given an attempt number, what's the backoff? No I/O, trivially unit-testable.
- **app/scheduler.py** — orchestrates one "process what's due" pass. Takes `now` as a
  parameter rather than reading the clock internally, so tests can drive retry timing
  deterministically.
- **app/models.py** — SQLAlchemy schema: `events` (one row per logical event, unique on
  `event_id`) and `delivery_attempts` (append-only history, one row per attempt).

Ingestion and delivery are deliberately separate: `POST /events` only ever writes one row
and returns — it never makes an outbound HTTP call itself, so the request path is fast and
the event is durably recorded before any external call is attempted.

## Technology choices

Python + FastAPI + SQLAlchemy + SQLite + httpx + pytest, as suggested in the brief and
matching my day-to-day stack. SQLite keeps setup to `pip install` with no external
database to stand up, appropriate for a 6–8 hour focused exercise; the repository layer is
the only place that touches SQL, so swapping in Postgres later is a one-line
`DATABASE_URL` change plus dropping the SQLite-specific `check_same_thread` connect arg.

A single background polling thread (not Celery/RQ/asyncio task queue) drives delivery.
This is simpler to read, debug, and test than a task-queue integration, and the brief
explicitly discourages introducing infrastructure (Celery, Redis, etc.) unless justified —
a single-process poller is sufficient for "one configured webhook endpoint," which is all
this exercise asks for.

Trade-off accepted: a polling loop has some latency (bounded by
`WEBHOOK_POLL_INTERVAL_SECONDS`) between an event becoming due and being picked up, versus
a push-based worker. For this exercise's scale that's an acceptable, explicit trade-off.

## Important decisions

1. **Idempotency is a database constraint, not an application check.** `events.event_id`
   has a `UNIQUE` constraint. `create_event_if_new` inserts directly and catches
   `IntegrityError` on conflict, falling back to a read of the existing row. This is the
   only design that's safe under concurrent duplicate submissions — an in-memory
   check-then-insert has a race window; the database does not.
2. **State transitions are explicit, not inferred.** `DeliveryState` is a fixed enum
   (`PENDING`, `IN_PROGRESS`, `DELIVERED`, `FAILED`) with documented valid transitions in
   `models.py`. Nothing infers "delivered" from the absence of a pending record — the
   state column is the single source of truth, always updated in the same transaction as
   the fact that changed it.
3. **Crash recovery via a stale-claim window, not a stronger guarantee.** An event claimed
   for delivery (`IN_PROGRESS`) but never resolved (process crash) is reclaimed once its
   claim is older than `WEBHOOK_STALE_CLAIM_SECONDS` (default 120s). This is a deliberately
   simple recovery mechanism appropriate to a single-process service — see Assumptions and
   Production sections for its trade-off.

## Decisions the assignment specifically asks to document

- **Which HTTP responses/errors are retryable:** 2xx = success. HTTP 429 and any 5xx are
  retryable ("temporary failure"). Any other 4xx (400, 404, 422, ...) is terminal — the
  request itself was rejected, retrying an identical payload won't change that. Connection
  errors, timeouts, and other transport-level failures (`httpx.TransportError` and
  subclasses) are treated as retryable, same as a 5xx.
- **Retry limit and backoff policy:** 5 attempts by default (`WEBHOOK_RETRY_MAX_ATTEMPTS`),
  exponential backoff `base * 2^(attempt-1)` capped at `WEBHOOK_RETRY_MAX_SECONDS` (default
  base 2s, cap 60s → attempts at ~2s, 4s, 8s, 16s after the prior failure, then terminal).
  All three values are environment-configurable, and tests override them to small numbers
  so retry logic is exercised without waiting.
- **Delivery guarantee:** at-least-once. Never exactly-once — see "duplicate delivery"
  below for the concrete mechanisms that make exactly-once impossible here, consistent with
  the brief's explicit statement that exactly-once across an HTTP boundary isn't required.
- **How concurrent duplicate submissions are handled:** the `UNIQUE` constraint on
  `events.event_id` is the actual guarantee (see Important Decisions #1). Two simultaneous
  `POST /events` calls with the same `eventId` will have exactly one succeed at the
  database level; the other observes the conflict and returns the same logical event with
  `created: false`. Verified directly in `test_idempotency.py` by committing two rows with
  the same `event_id` from two independent sessions.
- **What information is retained from each attempt:** attempt number (1-indexed, per
  event), timestamp, outcome (`success`/`failure`), the HTTP status code if one was
  received, and up to 1KB of the response body / error message otherwise. Stored as an
  append-only row in `delivery_attempts` — never updated, only inserted, so the history is
  always a faithful record of what actually happened.

## Assumptions and limitations

- Single webhook endpoint, single process — matches the brief's explicit scope (no multi-
  tenant, no multiple subscribers, no distributed queue).
- The demo/backoff timings (`WEBHOOK_POLL_INTERVAL_SECONDS`, `WEBHOOK_RETRY_BASE_SECONDS`)
  are meant to be shortened for local demonstration, per the brief's explicit allowance
  ("Retry delays may be shortened or controlled during tests and demonstrations"). Defaults
  are more conservative (1s poll, 2s base backoff).
- `occurredAt` is stored and echoed back as the caller-supplied string, not parsed into a
  strict datetime type — the brief allows field names/format flexibility and no behavior in
  this service depends on parsing it.
- Crash recovery (stale-claim reclaim, see Important Decisions #3) is bounded but not
  perfect: if the process crashes *after* a successful HTTP call to the receiver but
  *before* the `success` attempt is committed, the stale-claim reclaim will retry the
  event and the receiver will observe it a second time. This is an accepted consequence of
  at-least-once semantics over an HTTP boundary that can't itself be made transactional
  with the local database — deliberately not solved with a two-phase-commit-style
  mechanism, which would be disproportionate infrastructure for this exercise.
- No authentication, rate limiting, multiple endpoints, or management UI — explicitly out
  of scope per the brief.
- Tests use an in-memory-equivalent (temp-file) SQLite database per test and a fake HTTP
  transport (`httpx.MockTransport`); nothing touches the real network or a real receiver
  process.
- A live demo is deployed on Render's free tier (see link above). Free instances spin down after 15 minutes idle (first request after that takes ~30-60s to wake up) and have an ephemeral filesystem, so stored events reset on every redeploy or restart — this is a hosting-tier limitation, not an application bug. Verified working end-to-end on this deployment: successful delivery, retry-then-succeed, bounded failure to a genuinely unreachable host, and idempotent resubmission.

## Production and scale

**What could still cause a receiver to observe duplicate delivery?**
1. Fundamental to at-least-once semantics: if the receiver processes the request but the
   response is lost in transit (network partition, receiver crash after processing but
   before responding), the sender sees a transport error, classifies it as retryable, and
   retries — the receiver already got it once.
2. Specific to this implementation's crash-recovery mechanism: if this process crashes
   between receiving a successful HTTP response and committing the `success` attempt to
   the database, the stale-claim reclaim (see Important Decisions #3) will pick the event
   back up and deliver it again once the claim goes stale.
   Mitigation for receivers: treat delivery as at-least-once and de-duplicate on
   `eventId` on their side; this is standard webhook-consumer practice and is why the
   brief explicitly does not require exactly-once.

**How would you operate this with many workers?**
The current single-process poller has no coordination between instances — running two
copies would double-process the same due events, since claiming (`PENDING`→`IN_PROGRESS`)
and the SQLite session aren't safe across processes without additional locking. To run
multiple workers I would move to Postgres and change `list_due_events` /
`claim_for_delivery` into a single atomic claim using `SELECT ... FOR UPDATE SKIP LOCKED`
(or an equivalent `UPDATE ... WHERE state='pending' RETURNING`), so each worker claims a
disjoint set of due events per poll cycle. That's a change contained entirely to
`repository.py` — the scheduler and API layer wouldn't need to change.

**How would you prevent one failing endpoint from consuming all capacity?**
This exercise has exactly one endpoint, so this doesn't arise yet, but with multiple
subscriber endpoints I'd add: (a) a per-endpoint circuit breaker that pauses new attempts
to an endpoint after N consecutive failures and periodically probes it, instead of burning
worker time on an endpoint that's clearly down; (b) per-endpoint concurrency limits so one
slow/failing endpoint can't starve worker threads that other endpoints need; (c) separate
retry queues or priority so a backlog on one endpoint doesn't delay due deliveries for
others.

**What metrics and alerts would you add in production?**
Metrics: delivery attempts by outcome (success/failure) and status code, per-endpoint
success rate, time-to-delivery (created → delivered), retry count distribution, count of
events currently in each state (especially `FAILED`, which represents lost delivery
without manual intervention), stale-claim reclaim count (a proxy for process crash rate).
Alerts: `FAILED` rate above a threshold over a rolling window (signals a receiver outage or
a genuine integration break worth paging on), reclaim rate spiking (signals the service
itself is crashing), delivery queue depth / oldest-pending-event-age growing unboundedly
(signals the poller is falling behind or stuck).

## AI usage

Used Claude (Anthropic) throughout: architecture proposal and review, implementation of
all application and test code, and a dedicated review pass (Phase 5) that found and fixed
two real bugs before submission — events getting permanently stuck if the process crashed
mid-delivery, and one failing delivery being able to silently kill the background worker
thread. I reviewed, ran, and understand every part of the submitted code and can walk
through or modify any of it.

## Credibility note

At Answerbase, I worked as a Software Engineer Intern on the backend of a pre-launch SaaS product as part of a three-person backend team. I owned backend work around request validation and Redis-based caching, and helped redesign REST API contracts to reduce repeated work and improve response performance. I also added automated Jest coverage around the changes and worked directly with the existing backend architecture rather than building an isolated prototype.

One of the more important engineering decisions was deciding where caching and validation should sit in the request path: the goal was to reduce unnecessary backend work without weakening request correctness or making the API behavior difficult to reason about. This experience is relevant to the webhook challenge because it involved the same kind of concerns around API contracts, persistence, failure handling, and keeping backend behavior explicit and testable.

My public GitHub profile, including projects demonstrating my backend work, is:
https://github.com/Mohitkundu360.>
