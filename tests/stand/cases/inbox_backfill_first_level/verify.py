#!/usr/bin/env python3

from __future__ import annotations

from tests.stand import verify_lib as lib

DEPOSIT_LEVEL = 3102183
INBOX_COORDS = (3102183, 8)
RECEIVER = 'tz1KqTpEZ7Yob7QbPE4Hy4Wo8fHG8LhKxZSx'
AMOUNT_MUTEZ = '10000000'


def main() -> int:
    conn = lib.open_db('/tmp/bridge_inbox_backfill_first_level.sqlite')
    cur = conn.cursor()

    lib.section('Row counts')
    for t in ('rollup_inbox_message', 'l1_deposit', 'bridge_deposit', 'bridge_operation'):
        c = lib.count(cur, t)
        print(f'  {t:<22} {"(missing)" if c < 0 else c}')

    lib.section('Inbox messages (rollup_inbox_message, transfers only)')
    inbox = lib.rows(cur, 'SELECT level, "index", type FROM rollup_inbox_message WHERE type = \'transfer\' ORDER BY level, "index"')
    for r in inbox:
        print(f'  ({r["level"]}, {r["index"]})')

    lib.section('L1 deposits (l1_deposit)')
    l1 = lib.rows(cur, 'SELECT level, l2_account_id, amount FROM l1_deposit ORDER BY level')
    for r in l1:
        print(f'  lvl={r["level"]} l2={r["l2_account_id"]} amount={r["amount"]}')

    lib.section('bridge_deposit inbox attach')
    bridge = lib.rows(
        cur,
        'SELECT bd.inbox_message_id IS NOT NULL AS has_inbox, bo.status '
        'FROM bridge_deposit bd JOIN bridge_operation bo ON bo.id = bd.id ORDER BY bo.created_at',
    )
    for r in bridge:
        print(f'  inbox_attached={bool(r["has_inbox"])} status={r["status"]}')

    v = lib.Verdict()
    v.check(
        any((r['level'], r['index']) == INBOX_COORDS for r in inbox),
        f'inbox transfer {INBOX_COORDS} indexed (backfill started at/below the native ticket level)',
    )
    v.check(len(l1) == 1, 'exactly one L1 deposit indexed')
    v.check(bool(l1) and l1[0]['level'] == DEPOSIT_LEVEL, f'l1_deposit at level {DEPOSIT_LEVEL}')
    v.check(bool(l1) and l1[0]['l2_account_id'] == RECEIVER, f'l1_deposit.l2_account_id is {RECEIVER}')
    v.check(bool(l1) and l1[0]['amount'] == AMOUNT_MUTEZ, f'l1_deposit.amount is {AMOUNT_MUTEZ}')
    v.check(
        bool(bridge) and all(bool(r['has_inbox']) for r in bridge),
        'bridge_deposit has its inbox message attached (deposit is matchable)',
    )
    conn.close()
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
