"""Tezos X Michelson (tz1-receiver) XTZ-deposit support: pure helpers, no I/O.

An XTZ deposit routed to a Michelson `tz1` receiver lands on L2 as a synthetic
pseudo-Michelson `transaction` from TEZLINK_DEPOSITOR. The L1<->L2 key is the
synthetic op's hash, which the kernel derives deterministically from L1 inbox data:

    op_hash = base58check('o', keccak256(rlp([amount_wei, receiver, inbox_level,
                                              inbox_msg_id]) ++ raw_rollup_address))

All inputs are recoverable on the L1 side (ticket amount + routing receiver from the
inbox message; level/index = the inbox message coords; rollup address is constant),
so we compute the expected hash from L1 and match the observed L2 op by equality. The
derivation mirrors the Tezos X kernel (`etherlink/kernel_latest/` in gitlab.com/tezos/tezos);
correctness is pinned by golden vectors (tests/unit/tezos/test_michelson_deposit.py),
which fail if the kernel changes — not by the description here.

Versioning: the op-hash carries NO in-band version — it is tied to the kernel snapshot.
If a kernel changes the RLP shape, the 10**12 scaling, or the seed, this computes the
wrong hash fail-quiet (surfaces only as unmatched L2 deposits). Re-validate the golden
vectors against the active kernel, and pin params per kernel era if indexing across an
upgrade boundary. (`parse_routing_info`, by contrast, is version-gated in-band.)
"""

from __future__ import annotations

from dataclasses import dataclass

import rlp
from Crypto.Hash import keccak
from pytezos.crypto.encoding import base58_decode
from pytezos.crypto.encoding import base58_encode
from pytezos.michelson.forge import unforge_address

# mutez (L1, 6 decimals) -> wei (L2 XTZ, 18 decimals)
WEI_PER_MUTEZ = 10**12


@dataclass(frozen=True)
class DepositReceiver:
    """Parsed routing-data receiver.

    `kind`: 'evm' | 'tezos'. `address`: human form (`0x…` H160 / `tz1…`).
    `rlp_item`: the receiver as it sits in the kernel Deposit struct for the op-hash
    RLP — a 20-byte H160 (EVM) or `[b'\\x01', <22-byte contract>]` (Tezos).
    `chain_id`: None when absent.
    """

    kind: str
    address: str
    rlp_item: object
    chain_id: int | None = None


def parse_routing_info(raw: bytes) -> DepositReceiver:
    """Decode versioned routing data, mirroring the kernel's deposit routing format.

    - 20 bytes -> legacy: bare EVM address.
    - 52 bytes -> legacy: EVM address + 32-byte chain_id (little-endian).
    - else     -> `[version=1][rlp([receiver, option<chain_id>])]`; only version 1 is
      accepted. `receiver` is a scalar H160 (EVM) or `[tag=0x01, 22-byte Contract]`
      (Tezos; implicit tz1/tz2/tz3 only — KT1 is kernel-rejected). chain_id is
      little-endian, parsed for completeness but not part of the op-hash or the match.
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
    chain_id = int.from_bytes(chain_raw, 'little') if chain_raw else None  # little-endian

    if isinstance(receiver_item, list):
        # Tezos receiver [tag, 22-byte Contract]; unforge_address handles tz1/2/3 and KT1,
        # raising ValueError on an unknown tag — the exit the matcher expects.
        address = unforge_address(receiver_item[1])
        return DepositReceiver(kind='tezos', address=address, rlp_item=receiver_item, chain_id=chain_id)
    # EVM receiver: scalar H160
    return DepositReceiver(kind='evm', address='0x' + receiver_item.hex(), rlp_item=receiver_item, chain_id=chain_id)


def l2_account_from_routing_info(raw: bytes) -> tuple[str, str]:
    """The `l2_account` string `on_rollup_call` stores, plus its receiver kind.

    Returns `(l2_account, kind)` where `kind` is the `DepositReceiver.kind` literal
    (`'tezos'` or `'evm'`) so the caller can record the L2Account kind without re-deriving
    it from the address shape.

    v1 routing resolves to the receiver (`tz1…` base58 for Tezos, bare 40-hex for EVM).
    40B FA routing (receiver ++ proxy) is sliced up front — it is not versioned routing.
    Anything unparseable falls back to the legacy `raw[:20].hex()` slice so the deposit
    is still indexed.
    """
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
    """Expected L2 synthetic-tx op-hash for a stored rollup inbox `transfer` message.

    `message` is the `rollup_inbox_message.message` JSON; its `LL` arm holds the routing
    bytes and `ticket.amount`. `level`/`index` are the inbox message coords. Returns None
    when the message is not a Michelson (tz1-target) XTZ deposit (e.g. an EVM-target one,
    which lands on L2 as a real EVM tx).
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
    """Reconstruct the L2 synthetic-tx op-hash from L1 deposit data.

    `amount_mutez` is the ticket amount in mutez (scaled to wei here); `rollup_address`
    is the `sr1…` rollup the deposit went to. See module docstring for the formula.
    """
    # RLP order: amount (wei = mutez*10**12), receiver, inbox_level, inbox_msg_id.
    body = rlp.encode([amount_mutez * WEI_PER_MUTEZ, receiver.rlp_item, inbox_level, inbox_msg_id])
    # seed = raw rollup address, appended after the rlp; digest -> base58 'o' op-hash.
    seed = base58_decode(rollup_address.encode())
    digest = keccak.new(digest_bits=256, data=body + seed).digest()
    return base58_encode(digest, b'o').decode()
