"""
Database models for RazorRecover AI.

We use SQLite through SQLAlchemy instead of PostgreSQL for the hackathon
build. Same relational shape, zero setup for anyone cloning the repo.
Swapping to Postgres later is a one-line change (see database.py).
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
)
from sqlalchemy.orm import relationship
from .database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    risk_score = Column(Float, default=0.0)   # 0 = safe, 1 = high risk
    created_at = Column(DateTime, default=datetime.utcnow)

    payments = relationship("Payment", back_populates="customer")


class Payment(Base):
    """A single payment attempt (the '01 Customer Payment' box)."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    amount = Column(Float, nullable=False)
    currency = Column(String, default="INR")
    method = Column(String, nullable=False)   # card, upi, wallet, netbanking
    status = Column(String, default="failed")  # failed, reconciling, retrying, recovered, escalated, given_up
    failure_code = Column(String, nullable=True)   # e.g. INSUFF_FUNDS, or STATUS_UNKNOWN for stuck/ambiguous debits
    failure_reason = Column(String, nullable=True)

    # --- Compliance tracking (RBI Digital Payments - E-mandate Framework, 2026) ---
    reconciled = Column(Boolean, default=True)     # False until a STATUS_UNKNOWN payment's real gateway state is confirmed
    pre_debit_notice_sent = Column(Boolean, default=False)  # 24-hour pre-debit notice required before an auto-retry debit
    requires_afa = Column(Boolean, default=False)  # True when amount crosses the AFA threshold -> cannot be silently auto-retried

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="payments")
    attempts = relationship("RecoveryAttempt", back_populates="payment")
    audit_logs = relationship("AuditLog", back_populates="payment")


class RecoveryAttempt(Base):
    """Every retry / recovery action taken on a payment ('09 Action Executor')."""
    __tablename__ = "recovery_attempts"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"))
    attempt_number = Column(Integer, default=1)
    action = Column(String, nullable=False)       # retry_later, send_link, ...
    channel = Column(String, nullable=True)        # upi, card, sms, email
    outcome = Column(String, nullable=True)        # success, failure, pending
    confidence_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    payment = relationship("Payment", back_populates="attempts")


class AuditLog(Base):
    """Full trace of every AI decision -> the '12 Audit Log' box."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"))
    stage = Column(String, nullable=False)   # detect, reconcile, diagnose, policy, decide, execute, verify
    actor = Column(String, nullable=False)   # ai_diagnosis, policy_engine, recovery_agent, executor
    detail = Column(Text, nullable=False)    # human-readable explanation
    is_safe = Column(Boolean, default=True)  # False if guardrail blocked something
    regulation = Column(String, nullable=True)  # cites the real rule behind a compliance decision, if any
    created_at = Column(DateTime, default=datetime.utcnow)

    payment = relationship("Payment", back_populates="audit_logs")
