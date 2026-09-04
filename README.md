# RazorRecover AI — Autonomous Payment Recovery Agent

An AI agent that watches for failed payments, figures out *why* they failed,
proposes a safe recovery action, has that proposal checked against hard
guardrails, and only then acts — with a full audit trail for every decision.

Built for a hackathon, structured like production code.

## Why the stack looks simpler than the poster

The original architecture sketch used Spring Boot + PostgreSQL + RabbitMQ +
a separate Python AI worker — four moving parts, two languages. For a
solo/small-team hackathon build, that's a lot of infrastructure to set up,
demo, and explain under time pressure with no real upside in an MVP.

This build keeps the **same pipeline and the same responsibilities**, just
collapsed into one Python service so it's a `pip install` and one command
away from running:

| Poster component      | This build                          | Why |
|------------------------|--------------------------------------|-----|
| Spring Boot REST API   | FastAPI (`backend/app/main.py`)      | Same job (validate → route → respond), no JVM needed, and it's the same language as the AI layer so there's no serialization boundary between "API" and "AI" code. |
| PostgreSQL              | SQLite (`razorrecover.db`)          | Identical relational schema (see `models.py`). Swapping back to Postgres later is a one-line change in `database.py`. |
| RabbitMQ                | Direct function call (synchronous)  | The queue's job — decouple "event received" from "processing" — is simulated by calling the pipeline in-process. Easy to reintroduce a real queue later without touching business logic (see "Next steps" below). |
| Python AI Diagnosis     | `ai_diagnosis.py` — same stage, real code | Currently rule-based (transparent, deterministic, explainable to judges). Structured so a real LLM call is a drop-in replacement — see the docstring in that file. |

Nothing about the **pipeline stages, the guardrail logic, or the audit
trail** was simplified — that's the actual substance of the project.

## The pipeline (matches your poster's 12 stages)

```
01 Customer Payment ──► 02 Payment Failure Event ──► 03 REST API (FastAPI)
        ▼
04 Database (SQLite)  ◄──────────────► 05 Queue (in-process call, swappable for RabbitMQ)
        ▼
06 AI Diagnosis  →  07 Policy & Guardrail Engine  →  08 Recovery Agent
        ▼
09 Action Executor  →  10 Payment Simulator  →  11 Result Verification
        ▼
12 Audit Log + Dashboard  ──(feedback loop)──► back to 06 if retry allowed
```

Every stage above is a real, separate Python module — not a single script
pretending to be an architecture:

```
backend/app/
├── main.py             # 03: REST API + dashboard metrics endpoints
├── database.py         # 04: DB engine/session setup
├── models.py            # 04: Customer / Payment / RecoveryAttempt / AuditLog tables
├── ai_diagnosis.py      # 06: failure diagnosis + confidence scoring
├── policy_engine.py     # 07: guardrails — the safety layer
├── recovery_agent.py    # 08: turns diagnosis + policy into one final action
├── action_executor.py   # 09/10: executes the action, idempotently, against a simulated gateway
└── pipeline.py           # orchestrates 01-12 + the feedback loop
```

## The core design decision: propose vs. approve

The AI (`ai_diagnosis.py`) never acts directly. It only *proposes* an action
and a confidence score. `policy_engine.py` is the only thing allowed to
approve it, checking:

- **Retry limit** — never retry the same payment more than 3 times
- **Confidence threshold** — low-confidence diagnoses get escalated, not acted on
- **Transaction limit** — large amounts always go to a human, never auto-retried
- **Risk check** — high-risk customers are never auto-retried

If a proposal fails any check, the Recovery Agent doesn't silently drop it —
it escalates to a human. This is what lets the dashboard honestly show
**"0 unsafe actions."**

## Compliant escalation (RBI Digital Payments – E-mandate Framework, 2026)

The system doesn't just have internal safety limits (retry caps, confidence
thresholds) -- two of its guardrails are modeled on a real regulation:
RBI's **Digital Payments – E-mandate Framework, 2026** (effective 21 April
2026), which governs recurring/auto-debit retries in India.

1. **AFA threshold (₹15,000)** -- recurring debits above this amount
   require the customer to re-authenticate (OTP/PIN). The agent can never
   silently auto-retry above this line; `policy_engine.py` blocks it and
   `recovery_agent.py` reroutes to a **payment link** instead -- still
   automated, but the customer does the authenticating, which is compliant.
2. **24-hour pre-debit notice** -- customers must be notified before an
   auto-debit retry. This can't be waited out for real inside a demo, so
   the system enforces the *control*: `pipeline.notify_customer()` must
   run and succeed before a `retry_later` action can be approved. If the
   notice fails to deliver (simulated ~12% of the time, e.g. stale contact
   details), the payment is rerouted the same way -- to a payment link --
   rather than silently retried anyway.

Every one of these blocks is logged with the specific regulation it cites
(`AuditLog.regulation`), so a reviewer can trace exactly which decisions
were compliance-driven vs. ordinary risk/confidence guardrails. See
`GET /api/compliance/report` for a batch-level summary of how many times
each control actually fired.

**Reconciliation -- the "money stuck in the middle" case.** A payment can
fail *ambiguously*: the customer's account was debited, but the merchant
never got clean confirmation (a dropped webhook, a gateway timeout after
the bank already approved it). Retrying this blindly risks a double
charge. `reconciliation.py` enforces a hard rule: any payment tagged
`STATUS_UNKNOWN` must first have its real status confirmed by the gateway
before any recovery action is even considered. If the gateway confirms
the original charge actually succeeded, the payment is marked recovered
directly, with **zero** recovery attempts made -- proving the system
won't touch a payment it isn't sure about.

## Measuring recovery across a batch

`GET /api/compliance/report` returns a single, judge-facing summary:
total failed payments, how many were recovered, revenue recovered, and a
breakdown of every compliance control and reconciliation outcome that
fired during the run -- e.g.:

```json
{
  "batch_summary": { "total_failed_payments": 50, "payments_recovered": 35, "revenue_recovered": 685041.77, "recovery_rate_pct": 70.0 },
  "compliant_escalation": {
    "afa_blocks_triggered": 8,
    "rerouted_to_payment_link_instead_of_silent_retry": 9,
    "pre_debit_notices_successfully_sent": 10,
    "genuine_human_escalations": 2
  },
  "reconciliation": { "ambiguous_transactions_seen": 5, "double_charges_prevented": 3 }
}
```

This is the same data the dashboard's "Compliant escalation" panel
renders, generated fresh from whatever's actually in the database --
never hardcoded.

## Running it

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` — that's the dashboard.

### Proving it's really recovering, live — not just numbers on a screen

The dashboard has three controls at the top:

- **Start live simulation** — every ~2.5 seconds, one new realistic failed payment is generated and pushed through the *real* pipeline (diagnosis → policy → decision → execution → verification). Watch the **Live activity feed** on the right: it streams each decision as it happens, in the same dark terminal style you'd see in a real ops tool. The **Recovery rate**, **Revenue recovered**, and the transactions table update automatically as this runs. Nothing here is pre-recorded — click on any row's audit trail and you'll see the same reasoning chain the feed just showed you, because it's the same event.
- **Seed 40 at once** — for a quick bulk fill if you just want numbers to show without waiting.
- **Reset** — wipes all data back to zero, so you can start a demo (or a recording) from a clean state every time.

Because outcomes are computed by a probabilistic simulator standing in for a real gateway (see `action_executor.py`), you will see genuine variation — some payments recover immediately, some retry and then recover, some get escalated. That variation is what makes it *visibly* live rather than a canned animation.

### API endpoints (for testing without the UI)

- `POST /api/payments/fail` — report a failed payment (this kicks off the whole pipeline)
- `POST /api/payments/{id}/retry` — manually trigger the next feedback-loop cycle
- `GET  /api/payments` — list recent payments
- `GET  /api/payments/{id}/audit` — full decision trail for one payment
- `GET  /api/activity/recent` — cross-payment live feed (latest audit events, newest first)
- `GET  /api/metrics` — the numbers the dashboard shows
- `POST /api/demo/seed?count=40` — generate a batch of demo data at once
- `POST /api/demo/live-tick` — process exactly one new random failed payment (used by the live simulation toggle)
- `POST /api/demo/reset` — wipe all data back to zero

Example:
```bash
curl -X POST http://localhost:8000/api/payments/fail \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Aarav Sharma",
    "customer_email": "aarav@example.com",
    "amount": 2500,
    "method": "upi",
    "failure_code": "INSUFF_FUNDS",
    "customer_risk_score": 0.1
  }'
```

Valid `failure_code` values: `INSUFF_FUNDS`, `NETWORK_ERR`, `AUTH_FAIL`,
`EXPIRED`, `LIMIT_EXCEEDED`, `BANK_DECLINE`.

## Next steps if you keep building after this 

1. **Real LLM diagnosis** — replace the body of `ai_diagnosis.diagnose()`
   with an API call (Claude/GPT), keep the same return shape.
2. **Real queue** — swap the direct function call in `pipeline.py` for a
   RabbitMQ publish + a worker process that consumes it, for true async
   processing at scale.
3. **Postgres** — change one line in `database.py`.
4. **Real gateway** — replace `action_executor.execute()`'s simulated
   response with an actual Razorpay/Stripe retry/refund call.
5. **Auth** — add API key or JWT auth to `main.py`'s endpoints before this
   ever talks to a real payment gateway.
