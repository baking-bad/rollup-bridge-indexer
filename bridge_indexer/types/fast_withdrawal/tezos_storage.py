from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict


class Content(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    nat: str
    bytes: str | None = None


class Key(BaseModel):
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


class Value(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    paid_out: str


class Value1(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    cemented: dict[str, Any]


class Withdrawal(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    key: Key
    value: Value | Value1


class Config(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    xtz_ticketer: str
    smart_rollup: str
    expiration_seconds: str


class FastWithdrawalStorage(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    withdrawals: list[Withdrawal]
    config: Config
    metadata: dict[str, str]
