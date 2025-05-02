from typing import Literal

from pydantic import BaseModel

from bridge_indexer.types.tezos.forged_tezos_account import ForgedTezosAccount


class FastWithdrawalPayload(BaseModel):
    class Config:
        forbid: Literal['forbid'] = 'forbid'

    receiver: ForgedTezosAccount
    withdrawal_id: int
    amount: int
    timestamp: int
    payload: bytes
    l2_caller: str
