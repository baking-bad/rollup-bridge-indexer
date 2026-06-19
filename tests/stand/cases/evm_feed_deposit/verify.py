#!/usr/bin/env python3
"""Verdict for the evm-feed-deposit case: did the Tezos->EVM deposit index + match?"""

from __future__ import annotations

from tests.stand import verify_lib as lib


def main() -> int:
    conn = lib.open_db('/tmp/bridge_evm_feed_deposit.sqlite')
    cur = conn.cursor()

    lib.section('Row counts')
    for t in ('rollup_inbox_message', 'l1_deposit', 'l2_deposit', 'bridge_deposit', 'bridge_operation'):
        c = lib.count(cur, t)
        print(f'  {t:<22} {"(missing)" if c < 0 else c}')

    lib.section('L2 deposits (l2_deposit) — the Tezos->EVM side')
    for r in lib.rows(
        cur,
        'SELECT level, l2_account_id, amount, token_id, inbox_message_level, inbox_message_index FROM l2_deposit ORDER BY level LIMIT 20',
    ):
        print(
            f'  lvl={r["level"]} l2={r["l2_account_id"]} amount={r["amount"]} token={r["token_id"]} '
            f'inbox=({r["inbox_message_level"]},{r["inbox_message_index"]})'
        )

    lib.section('Matched bridge deposits')
    matched = lib.rows(
        cur,
        'SELECT bo.status, bo.type, bd.l1_transaction_id IS NOT NULL AS has_l1, '
        'bd.l2_transaction_id IS NOT NULL AS has_l2 '
        'FROM bridge_deposit bd JOIN bridge_operation bo ON bo.id = bd.id ORDER BY bo.created_at LIMIT 20',
    )
    for r in matched:
        print(f'  status={r["status"]} type={r["type"]} l1={bool(r["has_l1"])} l2={bool(r["has_l2"])}')

    v = lib.Verdict()
    v.check(lib.count(cur, 'l2_deposit') >= 1, 'l2_deposit indexed (Tezos->EVM deposit fired)')
    v.check(any(bool(r['has_l1']) and bool(r['has_l2']) for r in matched), 'bridge_deposit matched on both L1 and L2')
    conn.close()
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
