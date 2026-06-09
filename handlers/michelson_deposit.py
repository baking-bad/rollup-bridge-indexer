"""Tezos X Michelson (tz1-receiver) XTZ-deposit support.

Pure helpers shared by the L1 deposit handler and the bridge matcher. No I/O.

Background — why this exists instead of reading an event
--------------------------------------------------------
On the new Tezos X kernel an XTZ deposit whose routing data targets a Michelson
`tz1` receiver lands on L2 as a *synthetic* pseudo-Michelson `transaction` op
(``source = tz1Ke2h7…`` = TEZLINK_DEPOSITOR). The kernel attaches an internal
EMIT event (``tag=deposit``, payload ``Pair(inbox_level, inbox_msg_id)``) that
*would* give a deterministic L1<->L2 key — but TzKT intentionally drops events
emitted from an implicit (tz1) source, so that key is not observable via TzKT.

Instead we reconstruct the synthetic op's hash from L1 data. The L2 op-hash of
the synthetic transaction is deterministically derived by the kernel as::

    op_hash = base58check('o', keccak256(rlp([amount_wei, receiver, inbox_level,
                                              inbox_msg_id]) || raw_rollup_address))

All four struct fields are recoverable from the L1 side (TzKT
``/smart_rollups/inbox``): ``amount`` + ``receiver`` from the ticket + routing
data, ``inbox_level``/``inbox_msg_id`` = the inbox message's ``level``/``index``.
``raw_rollup_address`` is the rollup's address, a per-rollup constant. So we
compute the expected L2 op-hash on the L1 side and match the TzKT-observed L2
synthetic transaction by hash equality — deterministic, no node round-trip, no
dependence on the dropped event. Verified end-to-end against live previewnet
deposits (see tests/unit/tezos/test_michelson_deposit.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import rlp
from Crypto.Hash import keccak
from pytezos.crypto.encoding import base58_decode
from pytezos.crypto.encoding import base58_encode

# mutez (L1, 6 decimals) -> wei (L2 XTZ, 18 decimals)
WEI_PER_MUTEZ = 10**12

# Curve tag (2nd byte of a serialized implicit Contract) -> base58 prefix.
_IMPLICIT_CURVE_PREFIX = {0: b'tz1', 1: b'tz2', 2: b'tz3'}


@dataclass(frozen=True)
class DepositReceiver:
    """Parsed routing-data receiver.

    ``kind`` is ``'evm'`` or ``'tezos'``. ``address`` is the human form
    (``0x…`` H160 / ``tz1…``). ``rlp_item`` is the receiver exactly as it sits
    inside the kernel Deposit struct, ready to feed into the op-hash RLP:
    a 20-byte H160 for EVM, or the ``[b'\\x01', <22-byte contract>]`` list for
    Tezos. ``chain_id`` is ``None`` when absent.
    """

    kind: str
    address: str
    rlp_item: object
    chain_id: int | None = None


def parse_routing_info(raw: bytes) -> DepositReceiver:
    """Decode versioned routing data (``Deposit::parse_deposit_info``, bridge.rs).

    - 20 bytes  -> legacy v0: bare EVM address.
    - 52 bytes  -> legacy v1: EVM address + 32-byte chain_id (little-endian).
    - else      -> versioned RLP, first byte = version. Only version 1 is known:
      ``[version=1][rlp([receiver, option<chain_id>])]`` where ``receiver`` is
      either a scalar H160 (EVM) or ``[tag=0x01, 22-byte serialized Contract]``
      (Tezos; implicit ``tz1/tz2/tz3`` only — originated ``KT1`` is rejected by
      the kernel).
    """
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
    chain_id = int.from_bytes(chain_raw, 'big') if chain_raw else None

    if isinstance(receiver_item, list):
        # Tezos receiver: [tag, 22-byte serialized Contract]
        address = _serialized_contract_to_str(receiver_item[1])
        return DepositReceiver(kind='tezos', address=address, rlp_item=receiver_item, chain_id=chain_id)
    # EVM receiver: scalar H160
    return DepositReceiver(kind='evm', address='0x' + receiver_item.hex(), rlp_item=receiver_item, chain_id=chain_id)


def _serialized_contract_to_str(contract_bin: bytes) -> str:
    """22-byte serialized Tezos Contract -> base58 address string.

    Implicit: ``00 <curve> <20-byte pkh>`` -> ``tz1/tz2/tz3``.
    Originated: ``01 <20-byte hash> 00`` -> ``KT1`` (rejected upstream by the
    kernel, but decoded here for completeness / diagnostics).
    """
    if contract_bin[0] == 0:
        curve = contract_bin[1]
        try:
            prefix = _IMPLICIT_CURVE_PREFIX[curve]
        except KeyError:
            raise ValueError(f'unknown implicit curve tag: {curve}') from None
        return base58_encode(contract_bin[2:22], prefix).decode()
    if contract_bin[0] == 1:
        return base58_encode(contract_bin[1:21], b'KT1').decode()
    raise ValueError(f'unknown contract tag: {contract_bin[0]}')


def expected_op_hash_from_inbox(message: dict, level: int, index: int, rollup_address: str) -> str | None:
    """Expected L2 synthetic-tx op-hash for a stored rollup inbox `transfer` message.

    ``message`` is the ``rollup_inbox_message.message`` JSON (TzKT inbox parameter,
    ``micheline=0``): the ``LL`` arm holds the routing ``bytes`` and ``ticket.amount``.
    ``level``/``index`` are the inbox message's coordinates. Returns the op-hash to
    match against the L2 synthetic transaction, deterministically reconstructed from
    L1 data alone (see module docstring), or ``None`` when the message is not a
    Michelson (tz1-target) XTZ deposit — e.g. an EVM-target deposit, which lands on
    L2 as a real EVM tx, not a synthetic Michelson op.
    """
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
    """Reconstruct the L2 synthetic-transaction op-hash from L1 deposit data.

    ``amount_mutez`` is the ticket amount in mutez (scaled to wei internally).
    ``rollup_address`` is the ``sr1…`` rollup the deposit went to.
    """
    body = rlp.encode([amount_mutez * WEI_PER_MUTEZ, receiver.rlp_item, inbox_level, inbox_msg_id])
    seed = base58_decode(rollup_address.encode())
    digest = keccak.new(digest_bits=256, data=body + seed).digest()
    return base58_encode(digest, b'o').decode()
