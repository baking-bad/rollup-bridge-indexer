from __future__ import annotations

from pydantic import BaseModel
from pydantic import Extra
from pydantic import root_validator

from evm_indexer.types.forging import unforge_address


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

    @root_validator(pre=True)
    def pre_root(cls, values):
        if isinstance(values['receiver'], bytes):
            values['receiver'] = unforge_address(values['receiver'])
        return values
