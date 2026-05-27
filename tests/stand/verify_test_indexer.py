#!/usr/bin/env python3
"""Inspect the throwaway sqlite produced by the stripped tezosx-shadownet test indexer.

Focus: did a Tezos->EVM deposit get indexed and matched end-to-end?

Usage:
    uv run python verify_test_indexer.py [path-to-sqlite]
    # path defaults to $SQLITE_PATH, else /tmp/bridge_tezosx_test.sqlite
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _count(cur: sqlite3.Cursor, table: str) -> int:
    if not _table_exists(cur, table):
        return -1
    cur.execute(f'SELECT count(*) FROM {table}')
    return cur.fetchone()[0]


def _rows(cur: sqlite3.Cursor, sql: str) -> list[sqlite3.Row]:
    try:
        cur.execute(sql)
        return cur.fetchall()
    except sqlite3.OperationalError as exc:
        print(f'  (query failed: {exc})')
        return []


def _section(title: str) -> None:
    print()
    print(title)
    print('-' * len(title))


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('SQLITE_PATH', '/tmp/bridge_tezosx_test.sqlite')
    if not Path(path).exists():
        print(f'sqlite db not found: {path}\nrun `make test-indexer` first.')
        return 1

    print(f'DB: {path}')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    _section('Row counts')
    for t in (
        'tezos_ticket',
        'etherlink_token',
        'rollup_inbox_message',
        'rollup_outbox_message',
        'l1_deposit',
        'l2_deposit',
        'bridge_deposit',
        'bridge_operation',
    ):
        c = _count(cur, t)
        print(f'  {t:<24} {"(missing)" if c < 0 else c}')

    _section('rollup_inbox_message level span')
    for r in _rows(cur, 'SELECT min(level) lo, max(level) hi, count(*) n FROM rollup_inbox_message WHERE level > 0'):
        print(f'  levels {r["lo"]}..{r["hi"]}  ({r["n"]} transfer/external msgs)')

    _section('L1 deposits (l1_deposit)')
    for r in _rows(
        cur,
        'SELECT level, l1_account, l2_account, amount, ticket_hash, parameters_hash ' 'FROM l1_deposit ORDER BY level LIMIT 50',
    ):
        print(f'  lvl={r["level"]} l1={r["l1_account"]} l2={r["l2_account"]} amount={r["amount"]} ' f'phash={r["parameters_hash"]}')

    _section('L2 deposits (l2_deposit)  <-- the Tezos->EVM side')
    for r in _rows(
        cur,
        'SELECT level, l2_account, amount, token_id, ticket_hash, ticket_owner, '
        'inbox_message_level, inbox_message_index FROM l2_deposit ORDER BY level LIMIT 50',
    ):
        print(
            f'  lvl={r["level"]} l2={r["l2_account"]} amount={r["amount"]} token={r["token_id"]} '
            f'owner={r["ticket_owner"]} inbox=({r["inbox_message_level"]},{r["inbox_message_index"]})'
        )

    _section('Matched bridge deposits (bridge_deposit JOIN bridge_operation)')
    for r in _rows(
        cur,
        'SELECT bo.status, bo.type, bo.is_completed, bo.is_successful, '
        'bd.l1_transaction_id IS NOT NULL AS has_l1, '
        'bd.l2_transaction_id IS NOT NULL AS has_l2, '
        'bd.inbox_message_id IS NOT NULL AS has_inbox '
        'FROM bridge_deposit bd JOIN bridge_operation bo ON bo.id = bd.id ORDER BY bo.created_at LIMIT 50',
    ):
        print(
            f'  status={r["status"]} type={r["type"]} completed={r["is_completed"]} '
            f'ok={r["is_successful"]} l1={bool(r["has_l1"])} l2={bool(r["has_l2"])} inbox={bool(r["has_inbox"])}'
        )

    n_l2 = _count(cur, 'l2_deposit')
    _section('Verdict')
    if n_l2 > 0:
        print(f'  OK: {n_l2} L2 deposit(s) detected — Tezos->EVM deposit indexing fired.')
    else:
        print('  EMPTY: no L2 deposits detected in the window — the bug reproduces here.')
        print('  (check the etherlink deposit index `from_`/`contract` addresses vs. the kernel)')

    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
