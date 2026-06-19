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
    # Account NOT (yet) known to be an alias: there is no positive "native" proof for bridge
    # addresses, so evm/tz just mean an EVM/tz runtime address whose `origin` is itself.
    evm = 'evm'
    tz = 'tz'
    # Known alias: `origin` holds the native account it resolves to (`runtime_address` is the alias
    # form). evm_alias = an EVM address aliasing a tz origin (the only one we write).
    # tz_alias = a tz address aliasing an EVM origin; kept for completeness, never written.
    evm_alias = 'evm_alias'
    tz_alias = 'tz_alias'


class RuntimeKind(Enum):
    # The L2 runtime that processed the operation — the honest discriminator that
    # replaced the matcher's `transaction_hash`-prefix / inbox-coords-null heuristics.
    # evm = the EVM rollup runtime (real txs, wei-denominated, 0x receivers);
    # michelson = the Tezos X Michelson runtime (synthetic tz-receiver deposits,
    # mutez-denominated). Distinct from L2AccountKind, which classifies the *address*.
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
