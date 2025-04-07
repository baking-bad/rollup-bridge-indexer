from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class Content(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ticket_id: int = Field(validation_alias='nat')
    metadata_hex: str | None = Field(validation_alias='bytes')


class Ticket(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    address: str
    content: Content
    amount: int


class DefaultParameter(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    withdrawal_id: int
    ticket: Ticket
    timestamp: int
    base_withdrawer: str
    payload: bytes
    l2_caller: bytes
