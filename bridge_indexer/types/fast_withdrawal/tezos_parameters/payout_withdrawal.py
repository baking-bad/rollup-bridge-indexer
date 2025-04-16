from pydantic import BaseModel
from pydantic import ConfigDict


class Content(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    nat: str
    bytes: str | None = None


class Withdrawal(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    withdrawal_id: str
    full_amount: str
    ticketer: str
    content: Content
    timestamp: str
    base_withdrawer: str
    payload: str
    l2_caller: str


class PayoutWithdrawalParameter(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    withdrawal: Withdrawal
    service_provider: str
