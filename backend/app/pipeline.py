"""
The Full Processing Pipeline (stages 01-12 from the architecture diagram,
plus a reconciliation gate for ambiguous/stuck transactions).

process_failed_payment() is the single entry point that a Spring-Boot-style
REST endpoint (or, here, a FastAPI endpoint) calls after a payment fails.
It runs the whole Detect -> [Reconcile] -> Diagnose -> Decide -> Recover ->
Verify loop and writes a full audit trail as it goes, so nothing happens
invisibly.
"""
import random
from sqlalchemy.orm import Session
from . import models, ai_diagnosis, policy_engine, recovery_agent, action_executor, reconciliation

# Chance that a pre-debit notice fails to reach the customer (e.g. stale phone
# number/email on file). This is deliberately non-zero so the compliance
# guardrail in policy_engine (Rule 6) actually fires sometimes in a live
# demo, instead of being dead code that always passes.
NOTICE_DELIVERY_FAILURE_RATE = 0.12


def _log(db: Session, payment_id: int, stage: str, actor: str, detail: str, is_safe: bool = True, regulation: str = None):
    entry = models.AuditLog(payment_id=payment_id, stage=stage, actor=actor, detail=detail, is_safe=is_safe, regulation=regulation)
    db.add(entry)
    db.commit()


def process_failed_payment(db: Session, event) -> models.Payment:
    # --- Stage 01/02: Customer + Payment Failure Event ---
    customer = db.query(models.Customer).filter(models.Customer.email == event.customer_email).first()
    if not customer:
        customer = models.Customer(
            name=event.customer_name,
            email=event.customer_email,
            risk_score=event.customer_risk_score or 0.0,
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)

    is_ambiguous = event.failure_code == "STATUS_UNKNOWN"
    payment = models.Payment(
        customer_id=customer.id,
        amount=event.amount,
        method=event.method,
        status="reconciling" if is_ambiguous else "failed",
        failure_code=event.failure_code,
        reconciled=not is_ambiguous,   # ambiguous transactions start UNreconciled
        requires_afa=event.amount > policy_engine.AFA_THRESHOLD,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    if is_ambiguous:
        _log(db, payment.id, "detect", "spring_boot_api",
             f"Ambiguous event received: gateway response unclear on {event.method} for Rs. {event.amount} "
             f"-- money may have already left the customer's account")
        return run_reconciliation(db, payment.id)

    _log(db, payment.id, "detect", "spring_boot_api", f"Failure event received: {event.failure_code} on {event.method} for Rs. {event.amount}")
    return run_recovery_cycle(db, payment.id)


def run_reconciliation(db: Session, payment_id: int) -> models.Payment:
    """
    Mandatory gate for any payment whose true outcome is unknown (e.g. a
    dropped webhook after the bank had already approved the debit). No
    recovery action of any kind may run until this resolves the ambiguity
    -- see reconciliation.py for why this matters and how it's grounded.
    """
    payment = db.query(models.Payment).get(payment_id)
    reference_id = f"TXN-{payment.id:06d}"

    result = reconciliation.reconcile(payment.id, reference_id)
    _log(db, payment.id, "reconcile", "reconciliation", result.detail, is_safe=True)

    if result.status == "confirmed_success":
        payment.reconciled = True
        payment.status = "recovered"
        payment.failure_reason = "Money had already reached the merchant; confirmed via gateway reconciliation"
        db.commit()
        _log(db, payment.id, "verify", "result_verification",
             "No recovery action needed -- the original transaction actually succeeded. "
             "Marking recovered without touching the customer again.")
        return payment

    if result.status == "still_pending":
        payment.reconciled = False
        payment.status = "reconciling"
        db.commit()
        _log(db, payment.id, "verify", "result_verification",
             "Holding: gateway has not yet confirmed final status. No action taken this cycle. "
             "Re-check via POST /api/payments/{id}/retry once settlement completes.")
        return payment

    # confirmed_failed -> safe to proceed into the normal recovery pipeline.
    # Use the resolved failure code from the gateway (a real status API
    # would return this alongside the failure itself) so diagnosis has an
    # actual reason to work with, instead of falling back to "unclassified".
    payment.reconciled = True
    payment.status = "failed"
    if result.resolved_failure_code:
        payment.failure_code = result.resolved_failure_code
    db.commit()
    return run_recovery_cycle(db, payment.id)


def notify_customer(db: Session, payment: models.Payment) -> bool:
    """
    Simulates the RBI-required pre-debit notice (Digital Payments -
    E-mandate Framework, 2026): customers must be notified at least 24
    hours before an auto-debit retry attempt. A real 24-hour wait can't
    happen inside a demo, so this step stands in for it -- the important
    part is that the *step itself* is mandatory and logged, and that
    policy_engine refuses to approve a retry_later action without it.
    """
    delivered = random.random() > NOTICE_DELIVERY_FAILURE_RATE
    payment.pre_debit_notice_sent = delivered
    db.commit()
    if delivered:
        _log(db, payment.id, "notify", "compliance_notifier",
             f"Pre-debit notice sent to customer ({policy_engine.PRE_DEBIT_NOTICE_HOURS}h advance, per RBI e-mandate rules)",
             regulation=policy_engine.REGULATION_NOTICE)
    else:
        _log(db, payment.id, "notify", "compliance_notifier",
             "Pre-debit notice FAILED to deliver (stale contact details on file) -- retry cannot proceed this cycle",
             is_safe=False, regulation=policy_engine.REGULATION_NOTICE)
    return delivered


def run_recovery_cycle(db: Session, payment_id: int) -> models.Payment:
    """
    Runs ONE diagnose -> decide -> execute -> verify loop for a payment.
    Called initially on failure, and again by the feedback loop if the
    previous attempt failed and retries remain.
    """
    payment = db.query(models.Payment).get(payment_id)
    prior_attempts = db.query(models.RecoveryAttempt).filter(models.RecoveryAttempt.payment_id == payment_id).count()

    # --- Stage 06: AI Diagnosis ---
    diagnosis = ai_diagnosis.diagnose(
        failure_code=payment.failure_code,
        prior_attempt_count=prior_attempts,
        customer_risk_score=payment.customer.risk_score,
    )
    payment.failure_reason = diagnosis.failure_reason
    db.commit()
    _log(db, payment.id, "diagnose", "ai_diagnosis",
         f"Reason: {diagnosis.failure_reason} | confidence={diagnosis.confidence_score} | hint={diagnosis.action_hint}")

    # --- Compliance: pre-debit notice must be attempted before any retry_later can be approved ---
    if diagnosis.action_hint == "retry_later" and not payment.pre_debit_notice_sent:
        notify_customer(db, payment)

    # --- Stage 07: Policy & Guardrail Engine (includes compliance rules) ---
    policy = policy_engine.evaluate(
        proposed_action=diagnosis.action_hint,
        confidence_score=diagnosis.confidence_score,
        prior_attempt_count=prior_attempts,
        amount=payment.amount,
        customer_risk_score=payment.customer.risk_score,
        pre_debit_notice_sent=payment.pre_debit_notice_sent,
    )
    _log(db, payment.id, "policy", "policy_engine", policy.reason, is_safe=policy.approved, regulation=policy.regulation)

    # --- Stage 08: Recovery Agent decides final action ---
    decision = recovery_agent.decide(diagnosis, policy, payment.method)
    _log(db, payment.id, "decide", "recovery_agent", decision.explanation)

    # --- Stage 09/10: Action Executor + Simulator ---
    result = action_executor.execute(payment.id, prior_attempts + 1, decision.action, decision.channel)
    attempt = models.RecoveryAttempt(
        payment_id=payment.id,
        attempt_number=prior_attempts + 1,
        action=decision.action,
        channel=decision.channel,
        outcome=result.outcome,
        confidence_score=diagnosis.confidence_score,
    )
    db.add(attempt)
    db.commit()
    _log(db, payment.id, "execute", "action_executor", result.detail)

    # --- Stage 11: Result Verification ---
    if result.outcome == "success":
        payment.status = "recovered"
        _log(db, payment.id, "verify", "result_verification", "Payment recovered. Revenue restored.")
    elif decision.action in ("escalate_to_merchant", "do_nothing"):
        payment.status = "escalated"
        reg_note = f" (compliance: {policy.regulation})" if policy.regulation else ""
        _log(db, payment.id, "verify", "result_verification", f"Escalated to human / merchant for manual handling{reg_note}.")
    elif prior_attempts + 1 >= policy_engine.MAX_RETRY_ATTEMPTS:
        payment.status = "given_up"
        _log(db, payment.id, "verify", "result_verification", "Retry limit reached with no success. Marking as given up.")
    else:
        payment.status = "retrying"
        _log(db, payment.id, "verify", "result_verification", "Attempt failed. Feedback loop will evaluate next safe action.")

    db.commit()
    db.refresh(payment)
    return payment
