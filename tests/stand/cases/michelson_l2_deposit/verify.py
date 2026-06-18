#!/usr/bin/env python3
"""Verdict for the michelson-l2-deposit case: L2 deposit indexed + matched to its L1 op.

The Michelson deposit is stored in `l2_deposit` (L2DepositOperation, reused) and
matched to the L1 side by inbox coords.
"""

from __future__ import annotations

from tests.stand import verify_lib as lib


def main() -> int:
    conn = lib.open_db('/tmp/bridge_michelson_l2_deposit.sqlite')
    cur = conn.cursor()

    lib.section('Row counts')
    for t in ('rollup_inbox_message', 'l1_deposit', 'l2_deposit', 'bridge_deposit', 'bridge_operation'):
        c = lib.count(cur, t)
        print(f'  {t:<22} {"(missing)" if c < 0 else c}')

    lib.section('L1 deposits (l1_deposit)')
    for r in lib.rows(cur, 'SELECT level, l1_account, l2_account, amount FROM l1_deposit ORDER BY level LIMIT 20'):
        print(f'  lvl={r["level"]} l1={r["l1_account"]} l2={r["l2_account"]} amount={r["amount"]}')

    lib.section('bridge_operation (deposit side)')
    matched = lib.rows(
        cur,
        'SELECT bo.status, bo.type, bd.l1_transaction_id IS NOT NULL AS has_l1, '
        'bd.l2_transaction_id IS NOT NULL AS has_l2 '
        'FROM bridge_deposit bd JOIN bridge_operation bo ON bo.id = bd.id ORDER BY bo.created_at LIMIT 20',
    )
    for r in matched:
        print(f'  status={r["status"]} type={r["type"]} l1={bool(r["has_l1"])} l2={bool(r["has_l2"])}')

    v = lib.Verdict()
    # L1 side must work (confirms the window + on_rollup_call) — should PASS now.
    v.check(lib.count(cur, 'l1_deposit') >= 1, 'L1 deposit indexed (window + on_rollup_call OK)')
    # The feature target — RED now, GREEN after Block 1.
    v.check(lib.count(cur, 'l2_deposit') >= 1, 'L2 Michelson deposit indexed')
    v.check(any(bool(r['has_l1']) and bool(r['has_l2']) for r in matched), 'deposit matched on both L1 and L2')
    conn.close()
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
