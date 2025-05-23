from typing import Literal

from pydantic import BaseModel

from bridge_indexer.types.tezos.forged_tezos_account import ForgedTezosAccount


class WithdrawalPayload(BaseModel):
    class Config:
        forbid: Literal['forbid'] = 'forbid'

    amount: int
    sender: str
    receiver: ForgedTezosAccount
    withdrawal_id: int
