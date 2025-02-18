# generated by DipDup 8.0.0b5


from typing import Literal

from pydantic import BaseModel

from bridge_indexer.types.tezos.forged_tezos_account import ForgedTezosAccount


class WithdrawalPayload(BaseModel):
    class Config:
        forbid: Literal['forbid'] = 'forbid'

    ticket_hash: int
    sender: str
    ticket_owner: str
    receiver: ForgedTezosAccount
    proxy: ForgedTezosAccount
    amount: int
    withdrawal_id: int
