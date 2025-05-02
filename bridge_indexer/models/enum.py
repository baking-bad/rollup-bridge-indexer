from decimal import Decimal
from enum import Enum
from typing import Any

from asyncpg.pgproto import pgproto


def _custom_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, pgproto.UUID):
        return obj.hex
    raise TypeError


class RollupInboxMessageType(Enum):
    level_start: str = 'level_start'
    level_info: str = 'level_info'
    transfer: str = 'transfer'
    external: str = 'external'
    level_end: str = 'level_end'


class RollupOutboxMessageBuilder(Enum):
    kernel: str = 'kernel'
    service_provider: str = 'service_provider'


class BridgeOperationType(Enum):
    deposit: str = 'deposit'
    withdrawal: str = 'withdrawal'


class BridgeOperationKind(Enum):
    fast_withdrawal: str = 'fast_withdrawal'
    fast_withdrawal_claimed: str = 'fast_withdrawal_payed_out'
    fast_withdrawal_service_provider: str = 'fast_withdrawal_service_provider'


class BridgeOperationStatus(Enum):
    created: str = 'CREATED'
    finished: str = 'FINISHED'
    failed: str = 'FAILED'

    revertable: str = 'FAILED_INVALID_ROUTING_INFO_REVERTABLE'
    proxy_not_whitelisted: str = 'FAILED_INVALID_ROUTING_PROXY_NOT_WHITELISTED'
    empty_proxy: str = 'FAILED_INVALID_ROUTING_PROXY_EMPTY_PROXY'
    invalid_proxy: str = 'FAILED_INVALID_ROUTING_INVALID_PROXY_ADDRESS'
    inbox_matching_timeout: str = 'FAILED_INBOX_MATCHING_TIMEOUT'

    sealed: str = 'SEALED'
    outbox_expired: str = 'FAILED_OUTBOX_EXPIRED'
