from dipdup.context import HandlerContext
from dipdup.models.evm import EvmTransactionData
from eth_abi import decode
from eth_typing import HexStr

from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import L2AccountKind

# keccak256('Initialized(string,bytes,uint256)') — emitted exactly once per alias by the shared
# AliasForwarder code, from the alias address itself (log.address = alias). No indexed params:
# nativeAddress, nativePublicKey and forwardedBalance all live in `data`.
INITIALIZED_TOPIC0 = bytes.fromhex('60a9f8ac7be7e117b08e5ff52239667fcf051d55e03ead4bfa34c73ff86642e0')


async def on_alias_initialized(
    ctx: HandlerContext,
    transaction: EvmTransactionData,
) -> None:
    # DipDup can't wildcard-subscribe events (every alias emits from its own address), so the index
    # filters transactions by `from_` = TEZOSX_CALLER (kernel-only injector of init_tezosx_alias txs)
    # and we pull the receipt logs ourselves. Most such txs (claim_xtz, plain forwards) carry no
    # Initialized log and are skipped silently.
    datasource = ctx.get_evm_node_datasource('etherlink_node')
    receipt = await datasource.web3.eth.get_transaction_receipt(HexStr(transaction.hash))

    for log in receipt['logs']:
        topics = log['topics']
        if not topics or bytes(topics[0]) != INITIALIZED_TOPIC0:
            continue

        native_address, _native_public_key, _forwarded_balance = decode(['string', 'bytes', 'uint256'], bytes(log['data']))
        alias_address = log['address'].lower()[-40:]

        await L2Account.update_or_create(
            address=alias_address,
            defaults={
                'kind': L2AccountKind.evm_alias,
                'native_tz_address': native_address,
                'initialized_at_level': transaction.level,
                'initialized_at_tx': transaction.hash[-64:],
            },
        )

        ctx.logger.info('Alias initialized: %s -> %s (level %s)', alias_address, native_address, transaction.level)
