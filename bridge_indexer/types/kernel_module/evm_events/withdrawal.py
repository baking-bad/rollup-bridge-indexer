from __future__ import annotations

from pydantic import BaseModel
from pydantic import Extra

from bridge_indexer.types.tezos.forged_tezos_account import ForgedTezosAccount


class Withdrawal(BaseModel):
    class Config:
        extra = Extra.forbid

    ticket_hash: int
    sender: str
    ticket_owner: str
    receiver: ForgedTezosAccount
    amount: int
    outbox_level: int
    outbox_msg_id: int
