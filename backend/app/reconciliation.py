"""
Stage 05.5: Reconciliation (ambiguous / stuck transaction handling).

This module exists for one specific, important real-world case: a payment
where money left the customer's account, but the merchant never got clean
confirmation of what happened -- a dropped webhook, a gateway timeout after
the bank had already approved the debit, a mobile network drop mid-payment.

This is NOT the same as a clean failure (e.g. INSUFF_FUNDS, where nothing
was ever charged). Treating an ambiguous transaction the same as a clean
failure is dangerous: blindly "retrying" a payment that may have already
succeeded risks double-charging the customer.

The rule enforced here is simple and non-negotiable:
    A payment tagged STATUS_UNKNOWN can NEVER go straight to diagnosis/
    recovery. It must first be reconciled -- i.e. the real gateway must be
    asked "what actually happened to this transaction ID?" -- and only a
    confirmed non-success result is allowed to proceed to the normal
    diagnose -> decide -> recover pipeline.

In production this calls the payment gateway's transaction-status API
(e.g. Razorpay's "Fetch a Payment" endpoint) using the original idempotency
/ reference ID from the first attempt. Here, it's simulated the same way
the gateway itself is simulated elsewhere in this project -- but the
control-flow guarantee (never act on an unconfirmed transaction) is real
and is enforced by the policy engine, not just by convention.
"""
import random
from dataclasses import dataclass
from typing import Optional

GATEWAY_STATUS_OUTCOMES = ["confirmed_success", "confirmed_failed", "still_pending"]
# Weighted so most ambiguous cases resolve quickly, but some genuinely stay
# pending (e.g. bank-side settlement delay) -- these must be re-checked later,
# never assumed either way.
WEIGHTS = [0.35, 0.45, 0.20]

# When the gateway confirms a transaction genuinely failed, a real status API
# also returns *why* it failed (this is exactly what Razorpay's "Fetch a
# Payment" response includes, for example). Without this, downstream
# diagnosis has nothing concrete to reason about and falls back to a
# generic "unclassified failure" bucket -- technically safe (it escalates
# to a human rather than guessing) but wastes the diagnosis engine's
# ability to pick a real, targeted recovery action. Resolving a concrete
# reason here is what a production gateway integration would give you for
# free, so the simulation provides it too.
RESOLVED_FAILURE_CODES = ["INSUFF_FUNDS", "NETWORK_ERR", "AUTH_FAIL", "BANK_DECLINE"]


@dataclass
class ReconciliationResult:
    status: str                       # "confirmed_success" | "confirmed_failed" | "still_pending"
    safe_to_proceed: bool             # True only once we know for certain the money did NOT reach the merchant
    detail: str
    resolved_failure_code: Optional[str] = None  # set when confirmed_failed, so diagnosis has something real to work with


def reconcile(payment_id: int, reference_id: str) -> ReconciliationResult:
    """
    Queries the (simulated) gateway for the true status of a transaction
    that came back ambiguous. Idempotent by design: calling this again for
    the same reference_id should always be safe, since it is read-only --
    it never moves money, it only asks a question.
    """
    outcome = random.choices(GATEWAY_STATUS_OUTCOMES, weights=WEIGHTS, k=1)[0]

    if outcome == "confirmed_success":
        return ReconciliationResult(
            status=outcome,
            safe_to_proceed=False,
            detail=(
                f"Gateway confirms transaction {reference_id} actually succeeded. "
                f"No recovery action taken -- retrying would double-charge the customer. "
                f"Marking payment as recovered directly."
            ),
        )
    if outcome == "confirmed_failed":
        resolved_code = random.choice(RESOLVED_FAILURE_CODES)
        return ReconciliationResult(
            status=outcome,
            safe_to_proceed=True,
            resolved_failure_code=resolved_code,
            detail=(
                f"Gateway confirms transaction {reference_id} did not succeed "
                f"(resolved reason: {resolved_code}). Safe to proceed to normal diagnosis and recovery."
            ),
        )
    return ReconciliationResult(
        status=outcome,
        safe_to_proceed=False,
        detail=(
            f"Gateway reports transaction {reference_id} is still settling. "
            f"Holding -- no recovery action will be taken until status is confirmed."
        ),
    )
