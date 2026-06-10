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
dependence on the dropped event.

Kernel source — every step below was diffed against the Rust
------------------------------------------------------------
Etherlink / Tezos X smart-rollup kernel, ``etherlink/kernel_latest/`` in the Tezos
monorepo (canonical: gitlab.com/tezos/tezos; permalinks below via the GitHub mirror
``tezos/tezos-mirror`` pinned to commit ``1fa56773``). Cite the *symbol*, not the
line — line numbers drift, but the symbol names below are greppable.

  * Hashed struct + RLP field order: ``struct Deposit { amount: U256 /*wei*/,
    receiver, inbox_level: u32, inbox_msg_id: u32 }`` with ``#[derive(RlpEncodable)]``,
    and ``Deposit::hash(seed) = keccak256(rlp_bytes ‖ seed)`` (seed appended AFTER
    the RLP). -> kernel/src/bridge.rs
  * Seed = the 20-byte raw rollup address from reveal-metadata: ``handle_deposit``
    does ``deposit.hash(&host.reveal_metadata().raw_rollup_address)``. -> kernel/src/inbox.rs
  * ``amount`` is in WEI: ``eth_from_mutez(mutez) = mutez * 10**12`` is applied to the
    ticket amount before it lands in ``Deposit.amount``. -> ethereum/src/wei.rs
  * The 32-byte keccak digest is wrapped as ``OperationHash``; the ``o`` base58check
    prefix is the standard ``tezos_crypto_rs`` serialization (not hand-rolled), and
    the op is assembled carrying that ``tx_hash`` as its operation hash.
    -> kernel/src/apply.rs, kernel/src/chains.rs
  * Routing decode + receiver enum: ``Deposit::parse_deposit_info`` / ``DepositInfo::decode``
    / ``DepositReceiver`` (see ``parse_routing_info`` below). -> kernel/src/bridge.rs
  * The synthetic transfer's source ``TEZLINK_DEPOSITOR`` is the const ``[0u8; 22]``
    (all-zero serialized implicit Contract); ``tz1Ke2h7sDdakHJQh8WX4Z372du1KChsksyU``
    is just the base58 rendering of that all-zero tz1 pkh. -> kernel/src/bridge.rs

    https://github.com/tezos/tezos-mirror/blob/1fa56773/etherlink/kernel_latest/kernel/src/bridge.rs
    https://github.com/tezos/tezos-mirror/blob/1fa56773/etherlink/kernel_latest/kernel/src/inbox.rs
    https://github.com/tezos/tezos-mirror/blob/1fa56773/etherlink/kernel_latest/ethereum/src/wei.rs

What this depends on — READ before trusting it across a kernel upgrade
----------------------------------------------------------------------
The op-hash derivation has NO in-band version marker: nothing in the hash says which
kernel produced it. It is versioned only by *kernel snapshot* — the monorepo carries
frozen kernels (``kernel_bifrost``/``calypso``/``dionysus``/``ebisu``/``farfadet`` …)
beside ``kernel_latest``, and the tezlink/Michelson path is recent and still churning
(the pinned commit is itself a 2026-06 TezosX deposit fix). A future upgrade can
change the RLP shape, the ``10**12`` scaling, the seed, or the primitive at some block
height, and we would silently compute the wrong hash on one side of that boundary —
fail-quiet, surfacing only as unmatched L2 deposits, NOT an exception. If you ever
index across a kernel upgrade, treat these params as protocol-version-scoped (like
``ProtocolConstantStorage``): pin them to the kernel active in the indexed window and
add a golden vector per era. (Contrast ``parse_routing_info``, which IS version-gated
in-band by a version byte and so fails closed on an unknown version.)

Verified end-to-end against live previewnet deposits: two golden vectors from
different epochs both reproduce the TzKT-served L2 op-hash under this single
implementation (tests/unit/tezos/test_michelson_deposit.py), and the live stand case
``tests/stand/cases/michelson_l2_deposit_ophash`` re-checks it against indexed data.
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
    """Decode versioned routing data — kernel ``Deposit::parse_deposit_info`` +
    ``DepositInfo::decode`` (length dispatch then a version byte) and the
    ``DepositReceiver`` enum's RLP decode, in ``kernel/src/bridge.rs``:
    https://github.com/tezos/tezos-mirror/blob/1fa56773/etherlink/kernel_latest/kernel/src/bridge.rs

    - 20 bytes  -> legacy v0: bare EVM address.
    - 52 bytes  -> legacy: EVM address + 32-byte chain_id (little-endian,
      ``U256::from_little_endian``).
    - else      -> versioned RLP, first byte = version. Only version 1 is accepted
      (kernel rejects others); ``[version=1][rlp([receiver, option<chain_id>])]``
      where ``receiver`` is either a scalar H160 (EVM) or ``[tag=0x01, 22-byte
      serialized Contract]`` (Tezos; implicit ``tz1/tz2/tz3`` only — originated
      ``KT1`` is rejected: "Deposit to smart contracts are disabled"). The RLP
      ``chain_id`` is little-endian too (kernel ``decode_field_u256_le``).

    NB the version byte gates ONLY the routing encoding, not the op-hash derivation
    (which carries no version — see module docstring). ``chain_id`` is parsed for
    completeness but is not part of the op-hash or the L1<->L2 match.
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
    # little-endian: kernel reads this field with `decode_field_u256_le` (bridge.rs).
    chain_id = int.from_bytes(chain_raw, 'little') if chain_raw else None

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


def l2_account_from_routing_info(raw: bytes) -> str:
    """The ``l2_account`` string ``on_rollup_call`` should store for this routing data.

    Versioned routing (v1 RLP) resolves to the real receiver — ``tz1…`` base58 for a
    Tezos target, bare 40-hex (no ``0x``, matching the historical column format) for an
    EVM target. Legacy shapes keep the historical behavior byte-identical:

    - 20B (bare EVM) / 52B (EVM + chain_id) — handled by ``parse_routing_info``;
    - 40B FA routing (receiver ‖ proxy) is NOT versioned routing — ``parse_routing_info``
      would misread byte 0 as a version tag, so it is sliced up front like before;
    - anything unparseable falls back to the legacy ``raw[:20].hex()`` slice rather than
      raising, so an exotic deposit is still indexed (it just can't be routed precisely).
    """
    if len(raw) == 40:
        return raw[:20].hex()
    try:
        receiver = parse_routing_info(raw)
    except ValueError:
        return raw[:20].hex()
    if receiver.kind == 'evm':
        return receiver.address.removeprefix('0x')
    return receiver.address


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

    Mirrors kernel ``Deposit::hash`` (``kernel/src/bridge.rs``); each line maps 1:1
    to the Rust — see the module docstring for the pinned permalinks and the
    versioning caveat. ``amount_mutez`` is the ticket amount in mutez (scaled to wei
    internally). ``rollup_address`` is the ``sr1…`` rollup the deposit went to.
    """
    # RLP field order == `struct Deposit { amount /*wei*/, receiver, inbox_level, inbox_msg_id }`
    # (`#[derive(RlpEncodable)]`); amount is wei = mutez * 10**12 (`eth_from_mutez`, wei.rs).
    body = rlp.encode([amount_mutez * WEI_PER_MUTEZ, receiver.rlp_item, inbox_level, inbox_msg_id])
    # seed = the raw 20-byte rollup address (`handle_deposit`, inbox.rs); appended AFTER the rlp.
    seed = base58_decode(rollup_address.encode())
    digest = keccak.new(digest_bits=256, data=body + seed).digest()
    # 32-byte keccak digest -> `OperationHash` (`o` is the standard base58 prefix, not hand-rolled).
    return base58_encode(digest, b'o').decode()
