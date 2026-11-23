from __future__ import annotations

from dataclasses import dataclass

import rlp
from Crypto.Hash import keccak
from pytezos.crypto.encoding import base58_decode
from pytezos.crypto.encoding import base58_encode
from pytezos.michelson.forge import unforge_address

WEI_PER_MUTEZ = 10**12


@dataclass(frozen=True)
class DepositReceiver:

    kind: str
    address: str
    rlp_item: object
    chain_id: int | None = None


def parse_routing_info(raw: bytes) -> DepositReceiver:
    if len(raw) == 20:
        return DepositReceiver(kind='evm', address='0x' + raw.hex(), rlp_item=raw)
    if len(raw) == 52:
        return DepositReceiver(
            kind='evm',
            address='0x' + raw[:20].hex(),
            rlp_item=raw[:20],
            chain_id=int.from_bytes(raw[20:], 'little'),
        )

    if not raw:
        raise ValueError('empty routing data')
    version = raw[0]
    if version != 1:
        raise ValueError(f'unsupported routing-data version: {version}')

    try:
        decoded = rlp.decode(raw[1:])
    except rlp.exceptions.RLPException as exc:
        raise ValueError(f'malformed v1 routing data: {exc}') from exc
    if not isinstance(decoded, list) or not decoded:
        raise ValueError('malformed v1 routing data')
    receiver_item = decoded[0]
    chain_raw = decoded[1] if len(decoded) > 1 else b''
    chain_id = int.from_bytes(chain_raw, 'little') if chain_raw else None

    if isinstance(receiver_item, list):
        address = unforge_address(receiver_item[1])
        return DepositReceiver(kind='tezos', address=address, rlp_item=receiver_item, chain_id=chain_id)
    return DepositReceiver(kind='evm', address='0x' + receiver_item.hex(), rlp_item=receiver_item, chain_id=chain_id)


def l2_account_from_routing_info(raw: bytes) -> tuple[str, str]:
    if len(raw) == 40:
        return raw[:20].hex(), 'evm'
    try:
        receiver = parse_routing_info(raw)
    except ValueError:
        return raw[:20].hex(), 'evm'
    if receiver.kind == 'evm':
        return receiver.address.removeprefix('0x'), 'evm'
    return receiver.address, receiver.kind


def expected_op_hash_from_inbox(message: dict, level: int, index: int, rollup_address: str) -> str | None:
    arm = message.get('LL')
    if not arm:
        return None
    receiver = parse_routing_info(bytes.fromhex(arm['bytes']))
    if receiver.kind != 'tezos' or not receiver.address.startswith('tz'):
        return None
    amount_mutez = int(arm['ticket']['amount'])
    return compute_deposit_op_hash(amount_mutez, receiver, level, index, rollup_address)


def compute_deposit_op_hash(
    amount_mutez: int,
    receiver: DepositReceiver,
    inbox_level: int,
    inbox_msg_id: int,
    rollup_address: str,
) -> str:
    return base58_encode(_deposit_digest(amount_mutez, receiver.rlp_item, inbox_level, inbox_msg_id, rollup_address), b'o').decode()


def compute_evm_deposit_tx_hash(amount_mutez: int, receiver: str, inbox_level: int, inbox_msg_id: int, rollup_address: str) -> str:
    return _deposit_digest(amount_mutez, bytes.fromhex(receiver), inbox_level, inbox_msg_id, rollup_address).hex()


def _deposit_digest(amount_mutez: int, receiver_rlp_item: object, inbox_level: int, inbox_msg_id: int, rollup_address: str) -> bytes:
    body = rlp.encode([amount_mutez * WEI_PER_MUTEZ, receiver_rlp_item, inbox_level, inbox_msg_id])
    seed = base58_decode(rollup_address.encode())
    return keccak.new(digest_bits=256, data=body + seed).digest()
