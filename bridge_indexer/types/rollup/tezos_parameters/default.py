# generated by DipDup 8.0.0b3

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import RootModel


class TicketContent(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ticket_id: str = Field(validation_alias='nat')
    metadata_hex: str | None = Field(validation_alias='bytes')


class Ticket(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    address: str
    content: TicketContent
    amount: str


class LL(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    routing_info: str = Field(validation_alias='bytes')
    ticket: Ticket


class DefaultParameter1(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    LL: LL


class DefaultParameter2(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    LR: str


class DefaultParameter3(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    R: str


class DefaultParameter(RootModel[DefaultParameter1 | DefaultParameter2 | DefaultParameter3]):
    root: DefaultParameter1 | DefaultParameter2 | DefaultParameter3
