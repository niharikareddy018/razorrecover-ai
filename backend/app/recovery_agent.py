"""
Stage 08: Recovery Agent.

Turns a diagnosis + policy decision into one concrete, final action.
This is intentionally a thin layer: the AI (ai_diagnosis.py) proposes,
the policy engine (policy_engine.py) validates, and this module just
picks the final action word that the Action Executor understands.

Kept separate from ai_diagnosis on purpose -- in a real system you might
swap the diagnosis model (rules -> LLM -> fine-tuned classifier) without
ever touching decision/execution logic.

Compliant escalation, not just "give up":
When the policy engine blocks a silent retry for a COMPLIANCE reason
(AFA threshold or missing pre-debit notice -- see policy_engine.py), the
correct real-world response isn't to abandon the payment to a human. It's
to switch to a channel where the customer can re-authenticate themselves,
such as a payment link -- which satisfies RBI's AFA requirement (the
customer is doing the authenticating, not the agent) while still being an
automated recovery path. Escalation to an actual human is reserved for
guardrail blocks that aren't about compliance at all (retry limits, low
confidence, risk score, hard transaction ceiling).
"""
from dataclasses import dataclass
from typing import Optional
from .ai_diagnosis import Diagnosis
from .policy_engine import PolicyResult, REGULATION_AFA, REGULATION_NOTICE

VALID_ACTIONS = {
    "retry_later",
    "send_payment_link",
    "try_another_channel",
    "ask_customer_to_update",
    "escalate_to_merchant",
    "do_nothing",
}

COMPLIANCE_REGULATIONS = {REGULATION_AFA, REGULATION_NOTICE}


@dataclass
class RecoveryDecision:
    action: str
    channel: Optional[str]
    explanation: str


def decide(diagnosis: Diagnosis, policy: PolicyResult, method: str) -> RecoveryDecision:
    if not policy.approved:
        if policy.regulation in COMPLIANCE_REGULATIONS:
            # Compliant escalation: reroute to a self-authenticating channel
            # instead of a silent retry, rather than punting straight to a human.
            return RecoveryDecision(
                action="send_payment_link",
                channel=None,
                explanation=(
                    f"AI proposed '{diagnosis.action_hint}' but it was blocked on compliance grounds "
                    f"({policy.reason}). Rerouting to a payment link so the customer can "
                    f"re-authenticate directly -- compliant with {policy.regulation}."
                ),
            )
        # Non-compliance guardrail block (retry limit, low confidence, risk, hard ceiling)
        # -> genuine human escalation, never a silent retry.
        return RecoveryDecision(
            action="escalate_to_merchant",
            channel=None,
            explanation=f"AI proposed '{diagnosis.action_hint}' but guardrails rejected it ({policy.reason}). Escalating for human review.",
        )

    action = diagnosis.action_hint
    if action not in VALID_ACTIONS:
        action = "escalate_to_merchant"

    channel = None
    if action == "try_another_channel":
        # simple rotation: card -> upi -> netbanking -> wallet
        rotation = {"card": "upi", "upi": "netbanking", "netbanking": "wallet", "wallet": "card"}
        channel = rotation.get(method, "upi")
    elif action == "retry_later":
        channel = method

    return RecoveryDecision(
        action=action,
        channel=channel,
        explanation=f"Diagnosis '{diagnosis.failure_reason}' (confidence {diagnosis.confidence_score}) -> approved action '{action}'",
    )
