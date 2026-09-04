from pydantic import BaseModel
from typing import Optional


class PaymentFailureEvent(BaseModel):
    """Stage 02: what a real payment gateway webhook would send us."""
    customer_name: str
    customer_email: str
    amount: float
    method: str            # card | upi | wallet | netbanking
    failure_code: str      # INSUFF_FUNDS | NETWORK_ERR | AUTH_FAIL | EXPIRED | LIMIT_EXCEEDED | BANK_DECLINE
    customer_risk_score: Optional[float] = 0.0


class PaymentOut(BaseModel):
    id: int
    customer_name: str
    amount: float
    method: str
    status: str
    failure_code: Optional[str]
    failure_reason: Optional[str]

    class Config:
        from_attributes = True
