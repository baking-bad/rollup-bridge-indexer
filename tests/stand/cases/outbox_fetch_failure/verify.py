#!/usr/bin/env python3
"""outbox-fetch-failure verdict: a transient rollup-node failure must not cost outbox messages.

Run after the four-arm protocol in README.md (`run_arms.sh`, which calls this file itself and
propagates its exit code). The verdict is taken on the state left by the LAST arm — the
restart against a fully healthy rollup node:

  GREEN  both outbox messages of the window are in `rollup_outbox_message`, complete and
         usable (index/parameters_hash/cemented_level), i.e. the indexer either absorbed the
         failure or re-fetched the level after recovery.
  RED    the levels the failed drain popped are gone for good: the inbox cursor has moved
         past the external messages that enqueue them, so no later run ever asks for them.

The CONTROL arm alone also satisfies the check — that is the point of the differential: the
same verifier separates the arms.

Modes
  verify.py [SQLITE]                         the verdict above (`make inspect-test`)
  verify.py --facts ARM EXIT LEVELS STATUS   one JSON line of per-arm facts for run_arms.sh
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from tests.stand import verify_lib as lib

CASE_DIR = Path(__file__).resolve().parent


def load_window() -> dict[str, str]:
    """Parse `window.env` — the single home of this case's constants (see the file's header)."""
    values: dict[str, str] = {}
    for line in (CASE_DIR / 'window.env').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip()
    return values


W = load_window()

# Levels the window's external inbox messages enqueue that actually carry outbox messages.
EXPECTED_OUTBOX_LEVELS = tuple(int(x) for x in W['CASE_OUTBOX_LEVELS'].split(','))
EXPECTED_OUTBOX_ROWS = int(W['CASE_OUTBOX_ROWS'])

# The two transfers that straddle them (see window.env). Their ids are what strands the levels:
# the highest one becomes the resume cursor, and it is above the externals of both levels.
TRANSFER_IDS = tuple(int(x) for x in W['CASE_TRANSFER_IDS'].split(','))
STRANDED_EXTERNAL_IDS = {int(level): int(id_) for level, id_ in (p.split(':') for p in W['CASE_EXTERNAL_IDS'].split(','))}


def _db_path(argv: list[str]) -> str:
    return argv[0] if argv else os.environ.get('SQLITE_PATH', '/tmp/bridge_outbox_fetch_failure.sqlite')


def facts(arm: str, exit_code: int, fail_levels: str, fail_status: int, path: str) -> dict:
    """Machine-readable state of one arm: what it exited with, and what it left in the db."""
    out: dict = {
        'arm': arm,
        'exit_code': exit_code,
        'fail_levels': sorted({int(x) for x in fail_levels.replace(',', ' ').split()}),
        'fail_status': fail_status,
        'db_present': Path(path).exists(),
        'inbox_rows': -1,
        'outbox_rows': -1,
        'max_inbox_id': 0,
        'transfer_ids_present': [],
        'transfers_present': 0,
        'sentinel_ids': [],
        'outbox_levels': [],
        'outbox_level_present': {},
        'outbox_levels_missing': len(EXPECTED_OUTBOX_LEVELS),
    }
    if not out['db_present']:
        return out

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    out['inbox_rows'] = lib.count(cur, 'rollup_inbox_message')
    out['outbox_rows'] = lib.count(cur, 'rollup_outbox_message')
    inbox = [r['id'] for r in lib.rows(cur, 'SELECT id FROM rollup_inbox_message')] if out['inbox_rows'] >= 0 else []
    out['max_inbox_id'] = max(inbox, default=0)
    out['transfer_ids_present'] = [i for i in TRANSFER_IDS if i in set(inbox)]
    out['transfers_present'] = len(out['transfer_ids_present'])
    if out['inbox_rows'] >= 0:
        out['sentinel_ids'] = [r['id'] for r in lib.rows(cur, 'SELECT id FROM rollup_inbox_message WHERE level = 0 ORDER BY id')]
    if out['outbox_rows'] >= 0:
        out['outbox_levels'] = [r['level'] for r in lib.rows(cur, 'SELECT level FROM rollup_outbox_message ORDER BY level')]
    out['outbox_level_present'] = {str(level): level in set(out['outbox_levels']) for level in EXPECTED_OUTBOX_LEVELS}
    out['outbox_levels_missing'] = sum(1 for present in out['outbox_level_present'].values() if not present)
    conn.close()
    return out


def main(argv: list[str]) -> int:
    if argv and argv[0] == '--facts':
        arm, exit_code, fail_levels, fail_status = argv[1], int(argv[2]), argv[3], int(argv[4])
        print(json.dumps(facts(arm, exit_code, fail_levels, fail_status, _db_path(argv[5:]))))
        return 0

    conn = lib.open_db(_db_path(argv))
    cur = conn.cursor()

    lib.counts(cur, 'rollup_inbox_message', 'rollup_outbox_message')

    lib.section('rollup_outbox_message')
    outbox = lib.rows(cur, 'SELECT level, "index" AS idx, parameters_hash, cemented_level FROM rollup_outbox_message ORDER BY level')
    for r in outbox:
        print(f'  level={r["level"]}  index={r["idx"]}  parameters_hash={r["parameters_hash"]}  cemented_level={r["cemented_level"]}')

    inbox = lib.dump(cur, 'rollup_inbox_message', 'id, level, type', order_by='id')

    levels = {r['level'] for r in outbox}
    cursor_id = max((r['id'] for r in inbox), default=0)
    sentinels = [r['id'] for r in inbox if r['level'] == 0]

    lib.section('Resume cursor vs the levels the drain popped')
    print(f'  highest saved inbox id (next run resumes at id+1): {cursor_id}')
    print(f'  level=0 cursor sentinels: {sentinels or "none"}')
    for level, external_id in STRANDED_EXTERNAL_IDS.items():
        stranded = cursor_id > external_id and level not in levels
        print(f'  outbox level {level}: enqueued by inbox id {external_id} -> {"STRANDED" if stranded else "reachable/indexed"}')

    v = lib.Verdict()
    v.check(len(outbox) == EXPECTED_OUTBOX_ROWS, f'rollup_outbox_message holds exactly {EXPECTED_OUTBOX_ROWS} rows (got {len(outbox)})')
    v.check(levels == set(EXPECTED_OUTBOX_LEVELS), f'outbox levels are exactly {sorted(EXPECTED_OUTBOX_LEVELS)} (got {sorted(levels)})')
    for r in outbox:
        # A row that exists but cannot be matched or executed is not a recovered message.
        v.check(r['idx'] == 0, f'outbox level {r["level"]}: index == 0')
        v.check(r['parameters_hash'] is not None, f'outbox level {r["level"]}: parameters_hash is set (matchable)')
        v.check(r['cemented_level'] > r['level'], f'outbox level {r["level"]}: cemented_level {r["cemented_level"]} > level')

    # The permanence of the loss, asserted rather than printed: an outbox level may only be
    # missing while the inbox cursor still sits at or below the external message that queues
    # it. Nothing persisted — an inbox row OR a level=0 sentinel — may be above it.
    for level, external_id in STRANDED_EXTERNAL_IDS.items():
        v.check(
            level in levels or cursor_id <= external_id,
            f'outbox level {level} indexed, or still reachable (no inbox row/sentinel above id {external_id}; highest is {cursor_id})',
        )

    # The last arm must have actually advanced the backfill: `_process` writes a level=0
    # cursor sentinel at the id it stopped on, above the window's transfers. TREATMENT dies
    # before that, so this separates "the restart ran" from "the restart did nothing" — which
    # the inbox transfers cannot, they were committed by TREATMENT and survive by design.
    v.check(
        any(sentinel > max(TRANSFER_IDS) for sentinel in sentinels),
        f'the arm advanced the backfill past the window (level=0 sentinel above id {max(TRANSFER_IDS)}; got {sentinels or "none"})',
    )
    conn.close()
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
