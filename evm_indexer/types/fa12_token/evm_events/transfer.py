from __future__ import annotations

from pydantic import BaseModel
from pydantic import Extra
from pydantic import Field


class Transfer(BaseModel):
    class Config:
        extra = Extra.forbid

    from_: str = Field(..., alias='from')
    to: str
    value: int
