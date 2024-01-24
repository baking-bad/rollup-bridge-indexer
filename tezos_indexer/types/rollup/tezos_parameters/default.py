from __future__ import annotations

from pydantic import BaseModel
from pydantic import Extra


class Data(BaseModel):
    class Config:
        extra = Extra.forbid

    nat: str
    bytes: str | None


class Ticket(BaseModel):
    class Config:
        extra = Extra.forbid

    address: str
    data: Data
    amount: str


class LL(BaseModel):
    class Config:
        extra = Extra.forbid

    bytes: str
    ticket: Ticket


class DefaultParameter1(BaseModel):
    class Config:
        extra = Extra.forbid

    LL: LL


class DefaultParameter2(BaseModel):
    class Config:
        extra = Extra.forbid

    LR: str


class DefaultParameter3(BaseModel):
    class Config:
        extra = Extra.forbid

    R: str


class DefaultParameter(BaseModel):
    __root__: DefaultParameter1 | DefaultParameter2 | DefaultParameter3
