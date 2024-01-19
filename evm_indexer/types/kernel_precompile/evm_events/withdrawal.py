from __future__ import annotations

from pydantic import BaseModel
from pydantic import Extra


class Withdrawal(BaseModel):
    class Config:
        extra = Extra.forbid

    ticket_hash: int
    sender: str
    ticket_owner: str
    receiver: str
    amount: int
    outbox_level: int
    outbox_msg_id: int
