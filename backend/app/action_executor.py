"""
Stages 09 + 10: Action Executor + Payment/Recovery Simulator.

In a real deployment, `execute()` would call an actual payment gateway
(Razorpay/Stripe/etc). For the hackathon we simulate gateway responses
so the whole loop (fail -> diagnose -> decide -> act -> verify) can be
demoed end-to-end with no external accounts or API keys.

Idempotency: each call is keyed by (payment_id, attempt_number) so
re-running the same attempt never double-charges or double-sends --
this mirrors the "Atomic / Idempotent" label on the poster.
"""
import random
from dataclasses import dataclass
from typing import Optional

_executed_keys = set()  # in-memory idempotency guard for the demo


@dataclass
class ExecutionResult:
    outcome: str          # "success" | "failure" | "skipped_duplicate"
    detail: str


# Success probabilities per action, tuned to roughly land the dashboard's
# ~74% recovery rate over a realistic mix of failure types.
SUCCESS_PROBABILITY = {
    "retry_later": 0.55,
    "send_payment_link": 0.70,
    "try_another_channel": 0.65,
    "ask_customer_to_update": 0.40,
    "escalate_to_merchant": 0.0,   # not an automatic recovery
    "do_nothing": 0.0,
}


def execute(payment_id: int, attempt_number: int, action: str, channel: Optional[str]) -> ExecutionResult:
    key = (payment_id, attempt_number, action)
    if key in _executed_keys:
        return ExecutionResult("skipped_duplicate", "Idempotency guard: this exact action already ran")
    _executed_keys.add(key)

    if action in ("escalate_to_merchant", "do_nothing"):
        return ExecutionResult("pending_human", f"No automatic action taken ({action})")

    p_success = SUCCESS_PROBABILITY.get(action, 0.3)
    success = random.random() < p_success

    if success:
        return ExecutionResult("success", f"Simulated gateway accepted '{action}' via {channel or 'original method'}")
    return ExecutionResult("failure", f"Simulated gateway rejected '{action}' via {channel or 'original method'}")
