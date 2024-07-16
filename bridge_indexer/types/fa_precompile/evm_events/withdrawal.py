from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict


class WithdrawalPayload(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ticket_hash: int
    sender: str
    ticket_owner: str
    receiver: str
    amount: int
    withdrawal_id: int
