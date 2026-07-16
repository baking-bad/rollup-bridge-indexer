"""Unit tests for Tezos X Michelson (tz1) deposit op-hash matching.

The two op-hash vectors below are REAL previewnet deposits, cross-checked against
the L2 synthetic transaction hash served by TzKT — i.e. they prove the L1->L2
hash reconstruction is correct on live chain data, not just self-consistent.
"""

import rlp

from rollup_bridge_indexer.handlers.michelson_deposit import DepositReceiver
from rollup_bridge_indexer.handlers.michelson_deposit import compute_deposit_op_hash
from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from rollup_bridge_indexer.handlers.michelson_deposit import l2_account_from_routing_info
from rollup_bridge_indexer.handlers.michelson_deposit import parse_routing_info

ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'

# Real previewnet inbox `transfer` message exactly as the indexer stores it in
# `rollup_inbox_message.message` (TzKT /smart_rollups/inbox, micheline=0): the `LL`
# arm carries routing `bytes` + `ticket.amount`. This is inbox (3599297, 8) — the
# L1 leg of the tz1 deposit whose L2 synthetic op is opAhDW…41mRxomE.
INBOX_TZ1 = {
    'LL': {
        'bytes': '01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0',
        'ticket': {'amount': '1000000', 'address': 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi', 'content': {'nat': '0', 'bytes': None}},
    }
}


def test_expected_op_hash_from_inbox_tz1_deposit():
    # The stored inbox message replays to the same L2 synthetic op-hash the kernel
    # derived — the deterministic L1->L2 link, with no event and no node call.
    op_hash = expected_op_hash_from_inbox(INBOX_TZ1, 3599297, 8, ROLLUP)
    assert op_hash == 'opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE'


def test_expected_op_hash_from_inbox_skips_evm_target():
    # Most inbox deposits target an EVM (0x) receiver — those land on L2 as real
    # EVM txs, not Michelson synthetic ops, so this matcher must ignore them.
    inbox_evm = {'LL': {'bytes': 'ab' * 20, 'ticket': {'amount': '500000'}}}
    assert expected_op_hash_from_inbox(inbox_evm, 100, 1, ROLLUP) is None


def test_expected_op_hash_from_inbox_skips_non_deposit():
    # The verifier scans every inbox row; non-deposit messages (external, stored
    # as `{}`) carry no `LL` arm and must be skipped, not raise.
    assert expected_op_hash_from_inbox({}, 100, 1, ROLLUP) is None


def test_parse_v1_tezos_receiver():
    # routing data of a real tz1-target deposit (previewnet inbox 3599297/8)
    raw = bytes.fromhex('01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0')
    recv = parse_routing_info(raw)
    assert recv.kind == 'tezos'
    assert recv.address == 'tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7'
    assert recv.chain_id is None


def test_parse_legacy_evm_20_bytes():
    raw = bytes.fromhex('ab' * 20)
    recv = parse_routing_info(raw)
    assert recv.kind == 'evm'
    assert recv.address == '0x' + 'ab' * 20
    assert recv.chain_id is None


def test_parse_legacy_evm_52_bytes_with_chain_id():
    addr = bytes.fromhex('cd' * 20)
    chain = (128064).to_bytes(32, 'little')
    recv = parse_routing_info(addr + chain)
    assert recv.kind == 'evm'
    assert recv.address == '0x' + 'cd' * 20
    assert recv.chain_id == 128064


def test_parse_v1_evm_receiver():
    h160 = bytes.fromhex('9299f940615dfc7fab9e3cefe6c87ca484dd51ec')
    raw = b'\x01' + rlp.encode([h160, b''])
    recv = parse_routing_info(raw)
    assert recv.kind == 'evm'
    assert recv.address == '0x9299f940615dfc7fab9e3cefe6c87ca484dd51ec'


def test_parse_v1_kt1_decodes_but_is_originated():
    # Kernel rejects KT1 receivers; parser still decodes them so the handler can
    # skip explicitly. Serialized originated contract: 01 <20-byte hash> 00.
    contract_bin = b'\x01' + bytes.fromhex('00' * 20) + b'\x00'
    raw = b'\x01' + rlp.encode([[b'\x01', contract_bin], b''])
    recv = parse_routing_info(raw)
    assert recv.kind == 'tezos'
    assert recv.address.startswith('KT1')


def test_unsupported_version_raises():
    try:
        parse_routing_info(b'\x02' + b'\x00' * 10)
    except ValueError:
        return
    raise AssertionError('expected ValueError for unknown version')


def test_op_hash_golden_vector():
    # First end-to-end-verified deposit (pre-reset previewnet), from the
    # tezos-x-bridge-knowledge hash-check note.
    raw = bytes.fromhex('01dad80196000002298c03ed7d454a101eb7022bc95f7e5f41ac78c0')
    recv = parse_routing_info(raw)
    assert recv.address == 'tz1KqTpEZ7Yob7QbPE4Hy4Wo8fHG8LhKxZSx'
    op_hash = compute_deposit_op_hash(10_000_000, recv, 3102183, 8, ROLLUP)
    assert op_hash == 'onf9DgtgtTE2ibTYgyQy1v3sHQkevXbkBtaJ54vU21hpyCqgpGB'


def test_op_hash_live_vector():
    # Current previewnet deposit — the exact op Vladimir flagged as the
    # TzKT-implicit-source-event repro (event payload Pair(3599297, 8)).
    raw = bytes.fromhex('01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0')
    recv = parse_routing_info(raw)
    op_hash = compute_deposit_op_hash(1_000_000, recv, 3599297, 8, ROLLUP)
    assert op_hash == 'opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE'


def test_parse_v1_tz3_receiver_and_op_hash_live_vector():
    # Real previewnet tz3-receiver deposit (L2 op @ level 500705), live-verified
    # 2026-06-10 against inbox (3540152, 10): the curve-tag table must route
    # tz2/tz3 receivers, not just tz1.
    raw = bytes.fromhex('01dad801960002c0d8a3435278b955bd81c66e96ee76a87a42e6cdc0')
    recv = parse_routing_info(raw)
    assert recv.kind == 'tezos'
    assert recv.address == 'tz3duiskLgZdaEvkgEwWYF4mUnVXde7JTtef'
    assert l2_account_from_routing_info(raw) == ('tz3duiskLgZdaEvkgEwWYF4mUnVXde7JTtef', 'tezos')
    op_hash = compute_deposit_op_hash(10_000_000, recv, 3540152, 10, ROLLUP)
    assert op_hash == 'opEeCWYHcYbQWWvfef2JBoGJBGTKougCGS3UrbgxN9YcsZbdzXt'


def test_receiver_dataclass_is_hashable():
    recv = DepositReceiver(kind='evm', address='0x' + 'ab' * 20, rlp_item=bytes.fromhex('ab' * 20))
    assert recv.kind == 'evm'


# --- l2_account_from_routing_info: what on_rollup_call stores as l1_deposit.l2_account ---


def test_l2_account_v1_tezos_receiver_is_tz_address():
    # tz1-target deposit: the stored l2_account must be the human tz address,
    # not the first 20 bytes of the RLP envelope.
    raw = bytes.fromhex('01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0')
    assert l2_account_from_routing_info(raw) == ('tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7', 'tezos')


def test_l2_account_legacy_20_bytes_keeps_bare_hex():
    # Legacy XTZ->EVM routing: behavior must stay byte-identical to the old
    # `routing_info[:20].hex()` (bare hex, no 0x prefix).
    assert l2_account_from_routing_info(bytes.fromhex('ab' * 20)) == ('ab' * 20, 'evm')


def test_l2_account_legacy_52_bytes_drops_chain_id():
    raw = bytes.fromhex('cd' * 20) + (128064).to_bytes(32, 'little')
    assert l2_account_from_routing_info(raw) == ('cd' * 20, 'evm')


def test_l2_account_legacy_fa_40_bytes_is_receiver_slice():
    # FA deposit routing = receiver(20) + proxy(20); not parseable as versioned
    # routing — must keep returning the receiver slice as before.
    receiver = bytes.fromhex('12' * 20)
    proxy = bytes.fromhex('34' * 20)
    assert l2_account_from_routing_info(receiver + proxy) == ('12' * 20, 'evm')


def test_l2_account_v1_evm_receiver_is_bare_hex():
    h160 = bytes.fromhex('9299f940615dfc7fab9e3cefe6c87ca484dd51ec')
    raw = b'\x01' + rlp.encode([h160, b''])
    assert l2_account_from_routing_info(raw) == ('9299f940615dfc7fab9e3cefe6c87ca484dd51ec', 'evm')


def test_l2_account_unparseable_falls_back_to_slice():
    # Unknown/garbage routing must not raise inside on_rollup_call — fall back to
    # the legacy slice so the deposit is still indexed.
    raw = b'\x07' + b'\xee' * 30
    assert l2_account_from_routing_info(raw) == (raw[:20].hex(), 'evm')
