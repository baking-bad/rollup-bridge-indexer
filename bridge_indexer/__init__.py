from decimal import Decimal
from typing import Any

from asyncpg.pgproto import pgproto
from dipdup import utils


def _default_for_fix(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, pgproto.UUID):
        return str(obj)
    raise TypeError

utils._default_for_decimals = _default_for_fix
