#!/usr/bin/env python3
"""Shared helpers for per-case test-stand verifiers (`cases/<name>/verify.py`).

Generic sqlite plumbing (read row counts / rows, print sections) plus tiny assertion
helpers that build a pass/fail verdict. Each case's verify.py imports this, dumps the
tables it cares about, declares its expectations, and exits non-zero if any fail.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def open_db(default: str = '/tmp/bridge_test.sqlite') -> sqlite3.Connection:
    """Open the sqlite produced by a test-stand run.

    Path comes from argv[1], else $SQLITE_PATH, else `default`.
    """
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('SQLITE_PATH', default)
    if not Path(path).exists():
        print(f'sqlite db not found: {path}\nrun `make test-indexer CASE=<name>` first.')
        raise SystemExit(1)
    print(f'DB: {path}')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def count(cur: sqlite3.Cursor, table: str) -> int:
    """Row count, or -1 if the table is missing."""
    if not table_exists(cur, table):
        return -1
    cur.execute(f'SELECT count(*) FROM {table}')
    return cur.fetchone()[0]


def rows(cur: sqlite3.Cursor, sql: str) -> list[sqlite3.Row]:
    try:
        cur.execute(sql)
        return cur.fetchall()
    except sqlite3.OperationalError as exc:
        print(f'  (query failed: {exc})')
        return []


def section(title: str) -> None:
    print()
    print(title)
    print('-' * len(title))


class Verdict:
    """Accumulates pass/fail checks and renders a final verdict + exit code.

    Usage in a case verify.py:
        v = Verdict()
        v.check(lib.count(cur, 'l2_deposit') >= 1, 'l2_deposit indexed')
        raise SystemExit(v.report())
    """

    def __init__(self) -> None:
        self._results: list[tuple[bool, str]] = []

    def check(self, ok: bool, label: str) -> bool:
        self._results.append((bool(ok), label))
        return bool(ok)

    def report(self) -> int:
        section('Verdict')
        passed = True
        for ok, label in self._results:
            print(f'  [{"PASS" if ok else "FAIL"}] {label}')
            passed = passed and ok
        print(f'\n  => {"GREEN" if passed else "RED"}')
        return 0 if passed else 1
