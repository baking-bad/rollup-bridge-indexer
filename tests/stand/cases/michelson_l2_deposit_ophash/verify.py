#!/usr/bin/env python3
"""Verdict for the michelson-l2-deposit OP-HASH case — production matcher output.

This is the regression for the PRODUCTION op-hash path: `tezos.on_michelson_deposit`
records the full L2 row (xtz token, wei amount) and the separated matcher step
(`handlers/michelson_matcher.py`) links it to the L1 leg by op-hash equality,
reconstructed from inbox data alone (no event, no node call). GREEN means the real
`bridge_operation` is FINISHED with both legs and the inbox coords backfilled —
not a verifier-side replay.

A reconstruction spot-check (replaying `expected_op_hash_from_inbox` over the inbox
rows) is kept as a secondary assert so a matcher bug and a derivation bug are
distinguishable in the output.
"""

from __future__ import annotations

import json

from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from tests.stand import verify_lib as lib

# The rollup the deposits target (constant; same as the unit vectors / live demo).
ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'

# The verified on-chain pair (see README.md / window.env).
L2_OP_HASH = 'opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE'
RECEIVER = 'tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7'
INBOX_COORDS = (3599297, 8)
AMOUNT_MUTEZ = 1_000_000
AMOUNT_WEI = str(AMOUNT_MUTEZ * 10**12)


def _reconstructed_op_hashes(cur) -> dict[str, tuple[int, int]]:
    """expected L2 op-hash -> (inbox_level, inbox_index) for every tz1-target inbox row."""
    out: dict[str, tuple[int, int]] = {}
    for r in lib.rows(cur, 'SELECT level, "index", message FROM rollup_inbox_message ORDER BY level, "index"'):
        message = r['message']
        if isinstance(message, (str, bytes)):
            message = json.loads(message)
        op_hash = expected_op_hash_from_inbox(message, r['level'], r['index'], ROLLUP)
        if op_hash is not None:
            out[op_hash] = (r['level'], r['index'])
    return out


def main() -> int:
    conn = lib.open_db('/tmp/bridge_michelson_l2_deposit_ophash.sqlite')
    cur = conn.cursor()

    lib.section('Row counts')
    for t in ('rollup_inbox_message', 'l1_deposit', 'l2_deposit', 'bridge_deposit', 'bridge_operation'):
        c = lib.count(cur, t)
        print(f'  {t:<22} {"(missing)" if c < 0 else c}')

    lib.section('L1 deposits (l1_deposit)')
    l1 = lib.rows(cur, 'SELECT level, l1_account, l2_account, amount FROM l1_deposit ORDER BY level')
    for r in l1:
        print(f'  lvl={r["level"]} l1={r["l1_account"]} l2={r["l2_account"]} amount={r["amount"]}')

    lib.section('L2 Michelson deposits (l2_deposit)')
    l2 = lib.rows(
        cur,
        'SELECT level, transaction_hash, l2_account, amount, token_id, ticket_hash, '
        'inbox_message_level, inbox_message_index FROM l2_deposit ORDER BY level',
    )
    for r in l2:
        print(
            f'  lvl={r["level"]} op={r["transaction_hash"]} l2={r["l2_account"]} amount={r["amount"]} '
            f'token={r["token_id"]} inbox=({r["inbox_message_level"]},{r["inbox_message_index"]})'
        )

    lib.section('bridge_operation (matcher output)')
    bridge = lib.rows(
        cur,
        'SELECT bo.status, bo.type, bo.is_completed, bo.is_successful, bo.l2_account, '
        'bd.l1_transaction_id IS NOT NULL AS has_l1, bd.l2_transaction_id IS NOT NULL AS has_l2 '
        'FROM bridge_deposit bd JOIN bridge_operation bo ON bo.id = bd.id ORDER BY bo.created_at',
    )
    for r in bridge:
        print(
            f'  status={r["status"]} type={r["type"]} completed={bool(r["is_completed"])} '
            f'ok={bool(r["is_successful"])} l2={r["l2_account"]} l1_leg={bool(r["has_l1"])} l2_leg={bool(r["has_l2"])}'
        )

    expected = _reconstructed_op_hashes(cur)
    lib.section('Reconstruction spot-check (secondary)')
    for op_hash, (lvl, idx) in expected.items():
        print(f'  inbox=({lvl},{idx})  ->  {op_hash}')

    v = lib.Verdict()
    # L1 leg: indexed and routed to the real tz1 receiver (l2_account fix).
    v.check(len(l1) == 1, 'exactly one L1 deposit indexed')
    v.check(bool(l1) and l1[0]['l2_account'] == RECEIVER, f'l1_deposit.l2_account is the tz1 receiver ({RECEIVER})')
    # L2 leg: full consumer-visible row — xtz token, wei-scaled amount.
    v.check(len(l2) == 1, 'exactly one L2 Michelson deposit indexed')
    v.check(bool(l2) and l2[0]['transaction_hash'] == L2_OP_HASH, 'L2 row carries the synthetic op-hash')
    v.check(bool(l2) and l2[0]['token_id'] == 'xtz', 'L2 row carries the xtz token')
    v.check(bool(l2) and l2[0]['amount'] == AMOUNT_WEI, f'L2 amount is wei-scaled ({AMOUNT_WEI})')
    v.check(
        bool(l2) and (l2[0]['inbox_message_level'], l2[0]['inbox_message_index']) == INBOX_COORDS,
        f'inbox coords backfilled onto the L2 row {INBOX_COORDS}',
    )
    # The matcher verdict itself: FINISHED bridge operation with both legs.
    v.check(
        any(r['status'] == 'FINISHED' and bool(r['has_l1']) and bool(r['has_l2']) for r in bridge),
        'bridge_operation FINISHED with both legs (production matcher)',
    )
    v.check(any(r['l2_account'] == RECEIVER for r in bridge), 'bridge_operation.l2_account is the tz1 receiver')
    # Secondary: the derivation itself still reproduces the L2 op-hash.
    v.check(L2_OP_HASH in expected, 'inbox row reconstructs to the expected op-hash (derivation sane)')
    conn.close()
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
