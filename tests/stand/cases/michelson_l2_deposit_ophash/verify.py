#!/usr/bin/env python3
"""Verdict for the michelson-l2-deposit OP-HASH case.

Proves the deterministic L1<->L2 link *by reconstruction*: every indexed L1 inbox
`transfer` message is replayed through `expected_op_hash_from_inbox` (no event, no
node call), and the resulting op-hash must equal the op-hash of the L2 Michelson
deposit that our handler recorded. The match is done here in the verifier rather
than wired into the production matcher (reconstruction-only scope).
"""

from __future__ import annotations

import json

from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from tests.stand import verify_lib as lib

# The rollup the deposits target (constant; same as the unit vectors / live demo).
ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'


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
    for t in ('rollup_inbox_message', 'l1_deposit', 'l2_deposit'):
        c = lib.count(cur, t)
        print(f'  {t:<22} {"(missing)" if c < 0 else c}')

    expected = _reconstructed_op_hashes(cur)
    lib.section('L1 inbox -> reconstructed L2 op-hash (no event, no node call)')
    for op_hash, (lvl, idx) in expected.items():
        print(f'  inbox=({lvl},{idx})  ->  {op_hash}')

    lib.section('L2 Michelson deposits (l2_deposit) recorded by tezos.on_michelson_deposit')
    l2 = lib.rows(cur, 'SELECT level, transaction_hash, l2_account, amount FROM l2_deposit ORDER BY level')
    for r in l2:
        hit = '  MATCH' if r['transaction_hash'] in expected else '  no-match'
        print(f'  lvl={r["level"]} op={r["transaction_hash"]} l2={r["l2_account"]} amount={r["amount"]}{hit}')

    matched = [r for r in l2 if r['transaction_hash'] in expected]

    v = lib.Verdict()
    v.check(lib.count(cur, 'rollup_inbox_message') >= 1, 'rollup inbox backfilled (L1 inbox present)')
    v.check(len(expected) >= 1, 'an L1 inbox row reconstructs to a tz1-target op-hash')
    v.check(len(l2) >= 1, 'L2 Michelson deposit indexed (tezos.on_michelson_deposit fired)')
    v.check(len(matched) >= 1, 'L2 deposit op-hash matched its L1-reconstructed op-hash')
    conn.close()
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
