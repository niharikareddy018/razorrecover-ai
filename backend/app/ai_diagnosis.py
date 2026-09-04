"""
Stage 06: AI Diagnosis.

Takes the failed payment + customer history and produces:
  - a failure_reason (human readable)
  - a confidence_score (0-1)
  - a recommended_action hint (the Recovery Agent still makes the final call)

For the hackathon build this is a transparent, explainable rule engine
instead of a live LLM call -- judges can read exactly why a decision was
made, and it costs nothing to run live in a demo with no API key.

To upgrade to a real LLM later: replace the body of `diagnose()` with a
call to your model of choice, keep the same return shape
(reason, confidence, action_hint), and everything downstream
(policy engine, recovery agent) keeps working unchanged.
"""
from dataclasses import dataclass

FAILURE_CODES = {
    "INSUFF_FUNDS": {
        "reason": "Insufficient funds in customer's account at time of charge",
        "action_hint": "retry_later",
        "base_confidence": 0.82,
    },
    "NETWORK_ERR": {
        "reason": "Transient network/gateway timeout during authorization",
        "action_hint": "retry_later",
        "base_confidence": 0.90,
    },
    "AUTH_FAIL": {
        "reason": "Card/UPI authentication failed (wrong PIN/OTP/expired session)",
        "action_hint": "try_another_channel",
        "base_confidence": 0.65,
    },
    "EXPIRED": {
        "reason": "Card or payment instrument has expired",
        "action_hint": "ask_customer_to_update",
        "base_confidence": 0.95,
    },
    "LIMIT_EXCEEDED": {
        "reason": "Transaction exceeds the customer's daily/per-txn limit",
        "action_hint": "try_another_channel",
        "base_confidence": 0.78,
    },
    "BANK_DECLINE": {
        "reason": "Issuing bank declined the transaction with no further detail",
        "action_hint": "send_payment_link",
        "base_confidence": 0.55,
    },
}

DEFAULT_DIAGNOSIS = {
    "reason": "Unclassified failure code returned by gateway",
    "action_hint": "escalate_to_merchant",
    "base_confidence": 0.30,
}


@dataclass
class Diagnosis:
    failure_reason: str
    confidence_score: float
    action_hint: str


def diagnose(failure_code: str, prior_attempt_count: int, customer_risk_score: float) -> Diagnosis:
    """
    Pure function: same inputs -> same output. This determinism matters
    for a hackathon demo (reproducible) and for audit trails (explainable).
    """
    profile = FAILURE_CODES.get(failure_code, DEFAULT_DIAGNOSIS)
    confidence = profile["base_confidence"]

    # More prior attempts on the same failure -> less confident retrying will help
    confidence -= 0.08 * prior_attempt_count

    # Riskier customers (e.g. history of chargebacks) lower our confidence
    confidence -= 0.15 * customer_risk_score

    confidence = max(0.05, min(0.99, round(confidence, 2)))

    return Diagnosis(
        failure_reason=profile["reason"],
        confidence_score=confidence,
        action_hint=profile["action_hint"],
    )
