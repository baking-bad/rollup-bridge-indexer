from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict


class DepositPayload(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ticket_hash: int
    ticket_owner: str
    receiver: str
    amount: int
    inbox_level: int
    inbox_msg_id: int
