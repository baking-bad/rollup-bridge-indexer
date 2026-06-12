from enum import Enum


class RollupInboxMessageType(Enum):
    level_start = 'level_start'
    level_info = 'level_info'
    transfer = 'transfer'
    external = 'external'
    level_end = 'level_end'


class RollupOutboxMessageBuilder(Enum):
    kernel = 'kernel'
    service_provider = 'service_provider'


class L2AccountKind(Enum):
    evm = 'evm'
    tz = 'tz'
    evm_alias = 'evm_alias'


class BridgeOperationType(Enum):
    deposit = 'deposit'
    withdrawal = 'withdrawal'


class BridgeOperationKind(Enum):
    fast_withdrawal = 'fast_withdrawal'
    fast_withdrawal_claimed = 'fast_withdrawal_payed_out'
    fast_withdrawal_service_provider = 'fast_withdrawal_service_provider'


class BridgeOperationStatus(Enum):
    created = 'CREATED'
    finished = 'FINISHED'
    failed = 'FAILED'

    revertable = 'FAILED_INVALID_ROUTING_INFO_REVERTABLE'
    proxy_not_whitelisted = 'FAILED_INVALID_ROUTING_PROXY_NOT_WHITELISTED'
    empty_proxy = 'FAILED_INVALID_ROUTING_PROXY_EMPTY_PROXY'
    invalid_proxy = 'FAILED_INVALID_ROUTING_INVALID_PROXY_ADDRESS'
    inbox_matching_timeout = 'FAILED_INBOX_MATCHING_TIMEOUT'

    sealed = 'SEALED'
    outbox_expired = 'FAILED_OUTBOX_EXPIRED'
