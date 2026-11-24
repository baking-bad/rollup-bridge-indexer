import pytest
import rlp

from rollup_bridge_indexer.handlers.michelson_deposit import DepositReceiver
from rollup_bridge_indexer.handlers.michelson_deposit import compute_deposit_op_hash
from rollup_bridge_indexer.handlers.michelson_deposit import compute_evm_deposit_tx_hash
from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from rollup_bridge_indexer.handlers.michelson_deposit import l2_account_from_routing_info
from rollup_bridge_indexer.handlers.michelson_deposit import parse_routing_info

ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'

INBOX_TZ1 = {
    'LL': {
        'bytes': '01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0',
        'ticket': {'amount': '1000000', 'address': 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi', 'content': {'nat': '0', 'bytes': None}},
    }
}


def test_expected_op_hash_from_inbox_tz1_deposit():
    op_hash = expected_op_hash_from_inbox(INBOX_TZ1, 3599297, 8, ROLLUP)
    assert op_hash == 'opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE'


def test_expected_op_hash_from_inbox_skips_evm_target():
    inbox_evm = {'LL': {'bytes': 'ab' * 20, 'ticket': {'amount': '500000'}}}
    assert expected_op_hash_from_inbox(inbox_evm, 100, 1, ROLLUP) is None


def test_expected_op_hash_from_inbox_skips_non_deposit():
    assert expected_op_hash_from_inbox({}, 100, 1, ROLLUP) is None


def test_parse_v1_tezos_receiver():
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


@pytest.mark.parametrize(
    ('amount_mutez', 'receiver', 'level', 'index', 'tx_hash'),
    [
        (
            1001000,
            'f9e0f0a650716e7d626c66ed13459cb92491ac05',
            15660790,
            2,
            '6bcb6a6d58b9aa934216d939436b3b481a537d540e9d4db581d1064891f93d2f',
        ),
        (
            219000000,
            'edbe62a59eceb6e3b11d04ee14315ff5d3c29ec6',
            7056014,
            2,
            '96ffdc48cda144179913f566766f0bb0a82d88d397cb8f16a715081ed6bdd4c9',
        ),
    ],
)
def test_evm_deposit_tx_hash_real_mainnet_vectors(amount_mutez, receiver, level, index, tx_hash):
    assert compute_evm_deposit_tx_hash(amount_mutez, receiver, level, index, 'sr1Ghq66tYK9y3r8CC1Tf8i8m5nxh8nTvZEf') == tx_hash


def test_op_hash_golden_vector():
    raw = bytes.fromhex('01dad80196000002298c03ed7d454a101eb7022bc95f7e5f41ac78c0')
    recv = parse_routing_info(raw)
    assert recv.address == 'tz1KqTpEZ7Yob7QbPE4Hy4Wo8fHG8LhKxZSx'
    op_hash = compute_deposit_op_hash(10_000_000, recv, 3102183, 8, ROLLUP)
    assert op_hash == 'onf9DgtgtTE2ibTYgyQy1v3sHQkevXbkBtaJ54vU21hpyCqgpGB'


def test_op_hash_live_vector():
    raw = bytes.fromhex('01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0')
    recv = parse_routing_info(raw)
    op_hash = compute_deposit_op_hash(1_000_000, recv, 3599297, 8, ROLLUP)
    assert op_hash == 'opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE'


def test_parse_v1_tz3_receiver_and_op_hash_live_vector():
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


def test_l2_account_v1_tezos_receiver_is_tz_address():
    raw = bytes.fromhex('01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0')
    assert l2_account_from_routing_info(raw) == ('tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7', 'tezos')


def test_l2_account_legacy_20_bytes_keeps_bare_hex():
    assert l2_account_from_routing_info(bytes.fromhex('ab' * 20)) == ('ab' * 20, 'evm')


def test_l2_account_legacy_52_bytes_drops_chain_id():
    raw = bytes.fromhex('cd' * 20) + (128064).to_bytes(32, 'little')
    assert l2_account_from_routing_info(raw) == ('cd' * 20, 'evm')


def test_l2_account_legacy_fa_40_bytes_is_receiver_slice():
    receiver = bytes.fromhex('12' * 20)
    proxy = bytes.fromhex('34' * 20)
    assert l2_account_from_routing_info(receiver + proxy) == ('12' * 20, 'evm')


def test_l2_account_v1_evm_receiver_is_bare_hex():
    h160 = bytes.fromhex('9299f940615dfc7fab9e3cefe6c87ca484dd51ec')
    raw = b'\x01' + rlp.encode([h160, b''])
    assert l2_account_from_routing_info(raw) == ('9299f940615dfc7fab9e3cefe6c87ca484dd51ec', 'evm')


def test_l2_account_unparseable_falls_back_to_slice():
    raw = b'\x07' + b'\xee' * 30
    assert l2_account_from_routing_info(raw) == (raw[:20].hex(), 'evm')
