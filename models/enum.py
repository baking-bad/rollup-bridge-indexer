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


class OriginKind(Enum):
    # The `originOf` precompile's classification of an L2 address (see handlers/alias.py).
    # Classifies the address's *identity* — distinct from RuntimeKind, which is the runtime
    # that processed an *operation*. `origin`/`home_runtime` are only meaningful once the
    # address is classified; an `unknown` row is re-resolved until originOf gains a record.
    unknown = 'unknown'  # precompile kind 0 — no origin record (yet)
    native = 'native'  # precompile kind 1 — a native account of its own runtime
    alias = 'alias'  # precompile kind 2 — an alias of a native account in another runtime


class RuntimeKind(Enum):
    # The L2 runtime that processed the operation.
    # evm = the EVM rollup runtime (real txs, wei-denominated, 0x receivers);
    # michelson = the Tezos X Michelson runtime (synthetic tz-receiver deposits,
    # mutez-denominated). Distinct from OriginKind, which classifies the *address*. Also
    # reused as L2Account.home_runtime (the runtime an account's `origin` lives in).
    evm = 'evm'
    michelson = 'michelson'


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
