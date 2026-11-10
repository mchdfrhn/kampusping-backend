# Messaging: queue, planner, sender and WhatsApp gateway

How a reminder goes from an unpaid invoice to a WhatsApp message, and where each BRD rule is
enforced. Decisions: [ADR 0002](../../docs/adr/0002-baileys-whatsapp-gateway.md) (Baileys gateway)
and [ADR 0003](../../docs/adr/0003-database-backed-queue-and-worker-process.md) (database queue and
worker). Requirement codes (FR-xx, AB-xx, NFR-xx) refer to
[the BRD](../../docs/BRD-penagihan-uang-kuliah-whatsapp.md). Everything below describes milestone M1;
reply handling, manual/bulk send and exemption screens (M3) do not exist yet.

All business dates ("today", the send window, the planning window) use `APP_TIMEZONE`
(`Asia/Jakarta`); stored timestamps are UTC. The clock is injected (`Clock`), so rules never read
system time.

## Where the code is

| File (`app/modules/messages/` unless noted)        | Role                                                                                                        |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `model.py`, `constants.py`                         | `messages` table, statuses, cancel reasons, limits                                                          |
| `planner.py`, `repository.py`                      | Planning pass: escalate, expire, plan, supersede                                                            |
| `sender.py`, `send_rules.py`, `send_repository.py` | Send phase: gates, claim, FR-06 re-check, provider call, outcomes, circuit breaker, stale recovery          |
| `runner.py`                                        | One tick (plan then send) and the worker loop                                                               |
| `service.py`, `log_repository.py`, `router.py`     | Send log API, manual cancel, delivery receipts                                                              |
| `app/cli.py`                                       | `python -m app.cli run-worker [--once]`                                                                     |
| `app/modules/whatsapp/`                            | `WhatsAppProvider` contract, `GatewayProvider`, `FakeProvider`, status and logout endpoints, signed webhook |
| `app/modules/templates/rendering.py`               | Variables, message body and the opt-out footer                                                              |
| `app/modules/settings/`                            | The single `app_settings` row (window, cap, throttle, breaker, escalation)                                  |

## The worker in short

`python -m app.cli run-worker` loops every `WORKER_TICK_SECONDS` (60). One tick is:

1. **Plan** (`plan_messages`): create the due messages, cancel stale ones, commit.
2. **Send** (`run_send_phase`): recover stuck messages, then send one message at a time until the
   queue is empty or a gate closes.

A failure in either part is logged and does not stop the other part or the loop. `--once` runs one
tick, prints `Worker tick done: created=.. expired=.. superseded=.. escalated=.. handled=..` (counts
only, no personal data) and exits with code 1 if planning or the send phase raised. SIGTERM (POSIX)
or Ctrl+C ends the loop cleanly and closes the gateway's HTTP client. With `--once`, a send inside
the tick still waits the random throttle after the message.

**Run exactly one worker** (see [Single worker](#single-worker-rule)).

## Message lifecycle

```text
                 +--------------------------------------------------------------+
                 |  cancelled  (expired, superseded, escalated, manual, ...)    |
                 +------^----------------^--------------------------^-----------+
                        | planner/staff  | re-check (FR-06)         |
queued  ──claim──►  sending  ──ok──►  sent  ──receipt──►  delivered  ──receipt──►  read
   ▲                   │ │                 └────receipt "failed"──►  failed
   │                   │ └──permanent error / attempts exhausted──────►  failed
   └─── retry, AB-03 deferral, provider unavailable, stale recovery ───┘
```

| Status      | Meaning                                                                        | Set by                                      |
| ----------- | ------------------------------------------------------------------------------ | ------------------------------------------- |
| `queued`    | Waiting to be sent (also while backing off after a failure)                    | planner; sender when it puts a message back |
| `sending`   | Claimed by the sender; the provider call is in flight                          | sender (atomic claim)                       |
| `sent`      | The gateway accepted it and returned a `provider_message_id`                   | sender; stale recovery                      |
| `delivered` | Reached the phone (receipt)                                                    | webhook, only from `sent`                   |
| `read`      | Opened (receipt; needs read receipts on the recipient's phone)                 | webhook, from `sent` or `delivered`         |
| `failed`    | Not sent, or the receipt said it failed; `error` holds a short Indonesian text | sender; webhook (from `sent` only)          |
| `cancelled` | Will not be sent; `cancel_reason` says why                                     | planner, sender, staff                      |

Transitions in detail:

- `queued -> sending`: one conditional `UPDATE ... WHERE status='queued'` (and not soft-deleted).
- `sending -> sent`: the provider answered; `provider_message_id`, `sent_at`, `attempts + 1` stored.
- `sending -> queued`: the provider was unavailable (no attempt is burned), a retryable error with
  back-off (`next_attempt_at`), the AB-03 rule deferred it, or stale recovery without a provider id.
- `sending -> cancelled`: the FR-06 re-check refused it.
- `sending -> failed`: not on WhatsApp, request rejected, attempts exhausted, body longer than 4096
  characters, or an unexpected internal error.
- Late or duplicate receipts never move a message backwards (conditional updates): `delivered` after
  `read` is ignored.
- Only a `queued` message can be cancelled by staff (`POST /messages/{id}/cancel`, permission
  `messages:cancel`, audited); anything else answers `409`.
- The outcome of a provider call is written only while the row is still `sending`, so a message that
  staff cancelled during the call is not overwritten.

### Cancel reasons

`cancel_reason` is one of (`app/modules/messages/constants.py`):

| Reason          | Set when                                                                                           |
| --------------- | -------------------------------------------------------------------------------------------------- |
| `paid`          | Re-check found the invoice paid                                                                    |
| `opted_out`     | Re-check found the student opted out (nothing sets `opted_out_at` in M1)                           |
| `exempt`        | Re-check found an active exemption (no screen sets it in M1)                                       |
| `phone_invalid` | Re-check found no valid phone                                                                      |
| `expired`       | Planner: a queued auto message older than the 3-day planning window                                |
| `superseded`    | Planner: a newer queued message exists for the same invoice                                        |
| `escalated`     | The invoice passed the escalation offset (planner for queued messages, re-check for ones in retry) |
| `manual`        | Staff cancelled it, the student/invoice was soft-deleted (re-check), or an import rollback deleted the invoice (`error` = "Impor dibatalkan") |

### Import rollback and the queue

Rolling back an applied import (`POST /imports/{id}/rollback`, `imports/rollback.py`) soft-deletes
the invoices that import created and cancels their `queued` messages (`cancelled(manual)`). Messages
already `sent`/`delivered`/`read` stay in the send log: they cannot be recalled. A message in
`sending` is stopped by the FR-06 re-check (the invoice is deleted). Invoices the import only
changed go back to their previous values; an invoice that returns from paid to unpaid keeps its old
`cancelled(paid)` message, and because `(invoice_id, rule_id)` is unique the planner does not plan
that rule again, so no duplicate and no AB-03 breach.

## Planner rules (`planner.py`, `repository.py`)

The pass is idempotent: `messages` is unique on (`invoice_id`, `rule_id`), so running it twice, or
after downtime, never duplicates a message. Order inside one pass:

1. **Escalate** (AB-07): unpaid, non-exempt, not-yet-escalated invoices whose
   `due_date <= today - escalation_offset_days` (default 14) get `escalated_at`, and their queued
   automatic messages are cancelled with `escalated`. No further automatic message is planned for
   them; staff take over. (There is no follow-up list screen yet.)
2. **Expire**: queued automatic messages whose `scheduled_date < today - 3` become
   `cancelled(expired)`. Downtime longer than three days therefore loses reminders.
3. **Plan**: for each active rule whose template is also active, take invoices that are unpaid, not
   soft-deleted, not escalated, not exempt, whose student is not soft-deleted, not opted out and has
   `phone_status = valid`, and that have no message for that rule yet, with
   `today - 3 <= due_date + offset_days <= today` (the **3-day window**: a rule date that falls on a
   weekend is still planned on Monday). Each message gets `scheduled_date = due_date + offset_days`
   and a body **rendered at planning time** (template variables plus the footer
   `Balas STOP untuk berhenti.`); later edits to the template or settings do not change queued
   messages.
4. **Supersede**: per invoice only the queued automatic message with the latest `scheduled_date`
   (ties: highest id) survives; older ones become `cancelled(superseded)`. So a student who is late
   gets the current reminder, not a pile of old ones.

Planning does not look at the send window, the daily cap, `auto_send_enabled` or the connection:
messages are planned (and the log shows them as `Antre`) even when sending is off.

## Sender (`sender.py`, `send_rules.py`, `send_repository.py`)

Each loop iteration is one _step_ that handles at most one message. Settings are re-read at every
step, and the time is taken per step, so the send window is re-checked after the throttle pause.

### Gates (a closed gate means "idle": nothing is sent and nothing changes)

All of these must hold:

1. `auto_send_enabled` is true (default **false** until staff switch it on).
2. Sending is not paused (`paused_reason` is null, see the circuit breaker).
3. Today (local) is one of `working_days` (default Monday-Friday) and the local time is in
   `[window_start, window_end)` (default 08:00-17:00): **AB-04**.
4. The provider reports `connected`. If `status()` fails or says anything else, the whole send phase
   is skipped for this tick, no counter changes, and sending resumes by itself when the session is
   back. Messages stay `queued` meanwhile (and may expire after 3 days).
5. The daily cap is not reached: messages in `sending`, or `sent`/`delivered`/`read` with `sent_at`
   in the local day, are fewer than `daily_cap` (default 200).

### Picking and claiming

Candidates are `queued`, not soft-deleted, `scheduled_date <= today`, and not in back-off
(`next_attempt_at` empty or past). An automatic message of a student who already has another
automatic message `sending` or sent today is **not** a candidate (AB-03). Order: `scheduled_date`,
then the invoice's `due_date` (oldest first), then id. On PostgreSQL the row is selected
`FOR UPDATE SKIP LOCKED`; the claim itself is a conditional `UPDATE`, retried up to five times when
another process wins.

### FR-06 re-check (right after the claim)

The invoice and student are reloaded from the database and the message is refused if, in this order:

| Condition                                                           | Result                                                             |
| ------------------------------------------------------------------- | ------------------------------------------------------------------ |
| student or invoice soft-deleted                                     | `cancelled(manual)`, error "Mahasiswa atau tagihan sudah dihapus." |
| invoice `paid`                                                      | `cancelled(paid)` (**AB-01**)                                      |
| student `opted_out_at` set                                          | `cancelled(opted_out)` (**AB-06**)                                 |
| invoice has `exempt_reason` and `exempt_until` is empty or not past | `cancelled(exempt)` (**AB-05**)                                    |
| student phone not `valid` or empty                                  | `cancelled(phone_invalid)`                                         |
| automatic message and the invoice is escalated                      | `cancelled(escalated)` (**AB-07**)                                 |
| body longer than 4096 characters                                    | `failed`, "Pesan terlalu panjang."                                 |

Then AB-03 is checked once more (the claim could have raced another message of the same student):
a second automatic message that day goes back to `queued` and waits for the next working day.

### The call and its outcome

The provider is called with **no database transaction open** and
`client_ref = "msg-<message id>"` (the gateway's dedupe key). The outcome is then recorded in its own
short transaction:

| Provider result                                                                       | Message                                                                     | Attempts  | Counts towards the breaker | Phase                         |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | --------- | -------------------------- | ----------------------------- |
| success                                                                               | `sent`                                                                      | +1        | resets the counter to 0    | continues, after the throttle |
| `ProviderUnavailable` (gateway down, 503 disconnected, 401/403, 429, 5xx, bad answer) | back to `queued`                                                            | unchanged | +1                         | **stops** for this tick       |
| `NotOnWhatsApp`                                                                       | `failed`, the student's phone becomes `invalid`                             | +1        | no                         | continues                     |
| `ProviderRejected` (400/422, 409 `client_ref_conflict`)                               | `failed` at once                                                            | +1        | +1                         | continues                     |
| any other error                                                                       | back to `queued` with back-off, or `failed` when `attempts >= max_attempts` | +1        | +1                         | continues                     |

A counted failure that trips the circuit breaker also stops the phase. After a provider call the
sender sleeps a random time between `throttle_min_s` and `throttle_max_s`
(default 8-20 s), unless the phase has to stop anyway. Error texts stored in `messages.error` are
fixed Indonesian strings or a class name, never provider response bodies (they could hold personal
data).

An unexpected exception _after_ a claim marks that message `failed` with a generic error and the
phase moves on; an exception outside a claim (for example the database is down) ends the tick.
Nothing escapes `run_send_phase`.

### Retry and back-off (ruling R16)

`max_attempts` (setting, default 3, 1-10) counts the **total** provider attempts of one message, the
first one included. After the n-th failed attempt, if `n < max_attempts`, the message is re-queued
with `next_attempt_at = now + delay`, where the delay is 5, 15, 45 minutes for n = 1, 2, 3 (the last
value repeats). With the default of 3, a message is tried at t, t+5 min and t+20 min and then
`failed`: the 45-minute step only happens with `max_attempts` of 4 or more. (The design spec's "up
to 3 times" was ambiguous; this is the reading that matches the setting's name.)

### Circuit breaker and how to resume

Each counted failure increments `app_settings.consecutive_failures`; a success resets it. When it
reaches `consecutive_failure_limit` (default 5) the sender sets `paused_reason = 'circuit_breaker'`
and stops the phase. While `paused_reason` is set, gate 2 stays closed: **the pause does not clear
itself**, even when WhatsApp is healthy again. The dashboard and the Pengaturan page show
"Pengiriman otomatis dijeda: terlalu banyak kegagalan beruntun".

Resume: find and fix the cause (connection state, key mismatch, number restrictions), then press
**Lanjutkan pengiriman** in Pengaturan, which calls `POST /api/v1/settings/resume` (permission
`settings:update`). It clears `paused_reason`, resets the counter and writes an audit event
`settings.resumed`. Note the cost of the design: because a provider-unavailable _send_ counts and the
phase stops after one per tick, about five minutes of "gateway up but refusing sends" trips the
breaker. A gateway that is fully down or reports disconnected does not (gate 4).

### Stale recovery

At the start of every send phase, messages that sit in `sending` with `updated_at` older than 10
minutes belong to a worker that died. With a `provider_message_id` they become `sent` (the provider
accepted them); without one they go back to `queued`. A re-queued message may then be sent again; the
gateway's `client_ref` dedupe (below) is what prevents a duplicate WhatsApp message.

## Delivery receipts (webhook)

The gateway calls `POST /api/v1/webhooks/whatsapp` with `Content-Type: application/json` and
`X-Signature: sha256=<lowercase hex HMAC-SHA256 of the exact body, key WA_WEBHOOK_SECRET>`.

- No secret configured, a missing/malformed/wrong signature: `401` (one generic answer). Body over
  64 KB: `413`. Invalid JSON or a bad `status` event: `422`.
- `{"type":"status","provider_message_id":"...","state":"delivered"|"read"|"failed"}` applies the
  receipt (rules in [Message lifecycle](#message-lifecycle)) and answers `202`. An id the backend does
  not know also answers `202` and changes nothing.
- `connection` and `inbound` events and unknown types are acknowledged with `202` and ignored
  (replies arrive in M3).

**Dropped-receipt caveat.** The backend learns a message's `provider_message_id` only when the
worker commits the send outcome, so a receipt that arrives earlier finds nothing and is dropped with
`202`; the backend cannot ask for it again. The gateway therefore holds each receipt at least
`WA_STATUS_DELAY_MS` (2 s), retries non-2xx answers with exponential back-off, and replays every
delivered receipt once more after `WA_STATUS_REPLAY_MS` (30 s). If the backend's commit takes longer
than the delay plus the retries, or the gateway stops with events still in its in-memory queue, the
receipt is lost and the message stays `sent` even though it arrived. A message stuck at `sent` is
therefore **not** evidence of non-delivery.

## Gateway contract

`GatewayProvider` (`app/modules/whatsapp/gateway.py`) and the Node gateway
(`kampusping-wa-gateway/`, see its README) agree on this. Every call carries
`Authorization: Bearer <WA_GATEWAY_API_KEY>`; the gateway listens on `127.0.0.1:8095`.

| Call                                          | Success                                                                           | Failure mapping in the backend                                                                                                                                                                                                                                                                                           |
| --------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POST /messages` `{"to","text","client_ref"}` | `200 {"provider_message_id"}`                                                     | `404 {"error":"not_on_whatsapp"}` -> `NotOnWhatsApp`; `409 {"error":"client_ref_conflict"}` and `400 {"error":"invalid_request"}` (any other 4xx) -> `ProviderRejected` (permanent); `503 {"error":"disconnected"}`, 401/403, other 404, 429, 3xx, 5xx, timeouts, malformed answers -> `ProviderUnavailable` (transient) |
| `GET /status`                                 | `200 {"state":"connected"\|"connecting"\|"qr"\|"disconnected","qr":string\|null}` | any failure -> `ProviderUnavailable`                                                                                                                                                                                                                                                                                     |
| `POST /logout`                                | `200` or `204`; the gateway unlinks the device and starts a new pairing           | failure -> API answers `502`                                                                                                                                                                                                                                                                                             |

The gateway keeps `client_ref -> provider_message_id` in a durable file (at least 14 days). A repeated
`client_ref` returns the **original** id without sending again; the same `client_ref` with a different
recipient or text is refused with `409 client_ref_conflict` (message ids can repeat after a database
reset or restore). This is the safety net for every path that makes the worker resend: a lost answer,
a database failure right after a successful send, a crash, a stale-recovery re-queue. The only window
that can still duplicate is a gateway crash between WhatsApp accepting the message and the dedupe
record being written (milliseconds).

`WA_PROVIDER=fake` swaps in `FakeProvider` (in memory, always `connected`), used by tests, e2e and
the default compose stack. The API and the worker are separate processes, so each has its own fake
instance; nothing is shared and nothing reaches WhatsApp.

## Single worker rule

Run exactly one worker process (PM2 `kampusping-worker`: `instances: 1`, fork mode; compose:
one `worker` service, never `--scale`). The one-message-per-student-per-day check (AB-03) and the
per-process throttle are only safe with a single sender: two workers could both pass the AB-03
check before either records its message. The atomic claim (and `SKIP LOCKED` on PostgreSQL) stops two
workers sending the _same_ message, not two messages to one student. Use `run-worker --once` only
while the PM2 worker is stopped.

## Where each business rule is enforced

| Rule                                                                           | Enforced by                                                                                                                                                                                                                     | Code                                                                                        |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| **AB-01 / FR-06** only unpaid, re-checked right before sending; paid is sticky | Planner selects only `unpaid`; sender re-check cancels `paid`; import never turns a paid invoice back to unpaid                                                                                                                 | `repository.candidates`, `send_rules.recheck`, `app/modules/imports/apply.py`               |
| **AB-02** schedule H-7, H-3, H-1, H, H+3, H+7                                  | Rules are rows (`schedule_rules`), seeded once and edited in the UI; the planner reads the active ones                                                                                                                          | `app/modules/templates/seed.py`, `schedules/`, `planner.plan_messages`                      |
| **AB-03** one automatic message per student per day                            | Candidate query excludes students with an auto message `sending`/sent today; second check after the claim; supersede keeps one queued message per invoice                                                                       | `send_repository.auto_message_busy`, `sender._check_claimed`, `repository.supersede_queued` |
| **AB-04** send window (08:00-17:00, working days)                              | Gate 3 using `APP_TIMEZONE`, evaluated at every step                                                                                                                                                                            | `send_rules.schedule_open`                                                                  |
| **AB-05** exemptions get nothing                                               | Planner skips exempt invoices; re-check cancels `exempt`. **No endpoint or screen sets `exempt_reason` in M1** (M3), so this is dormant                                                                                         | `repository.is_exempt`, `send_rules.recheck`                                                |
| **AB-06 / FR-11** opt-out honoured; footer on every message                    | Planner skips opted-out students; re-check cancels `opted_out`; the footer `Balas STOP untuk berhenti.` is appended to every body at planning. **Reading a STOP reply and setting `opted_out_at` is M3**; nothing sets it in M1 | `send_rules.recheck`, `templates/rendering.with_opt_out_footer`                             |
| **AB-07** automatic messages stop after the escalation offset (H+14)           | Planner escalates and cancels; re-check cancels messages in retry                                                                                                                                                               | `planner`, `repository.escalate_invoices`, `send_rules.recheck`                             |
| **AB-08** polite tone                                                          | The default templates; staff own the wording                                                                                                                                                                                    | `templates/seed.py`                                                                         |
| **NFR-06** one failure never stops the queue                                   | Per-message transactions and outcomes; only the breaker pauses                                                                                                                                                                  | `sender.py`                                                                                 |
| **NFR-08** no direct SIAKAD/SIPEMA connection                                  | Data enters only through uploaded files                                                                                                                                                                                         | `app/modules/imports/`                                                                      |
| UU PDP                                                                         | Phone numbers masked to the last 4 digits in logs; SQL parameters hidden in errors (`hide_parameters`); stored errors are fixed texts                                                                                           | `app/shared/phone.py`, `app/db/session.py`                                                  |

## Running and checking it locally

```bash
export DATABASE_ENGINE=sqlite DATABASE_URL=sqlite+aiosqlite:///<a file outside the repo> WA_PROVIDER=fake
python -m alembic upgrade head
python -m app.cli create-admin          # also seeds templates and the six rules
uvicorn app.main:app --reload           # sign in, import a file, switch on Pengiriman otomatis
python -m app.cli run-worker --once     # inside the send window: plans and "sends" through the fake provider
```

Without a sign-in and an import there is nothing to plan, and with automatic sending off the send
phase stays idle. Real sending through the gateway is tested by hand with
[`docs/manual-smoke-test.md`](../../docs/manual-smoke-test.md); operations are in
[`docs/runbook-whatsapp.md`](../../docs/runbook-whatsapp.md).
