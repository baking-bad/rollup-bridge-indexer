#!/usr/bin/env python3
"""Turn progress.jsonl into the per-stage rate tables of REINDEX_PERF_GROUNDTRUTH.md.

The series spans more than one run: a wipe shows up as the inbox counter falling back,
so the run under measurement is the tail after the last such fall. Error rows carry no
counters but are kept in view — a gap in the series and a stalled indexer look alike,
and telling them apart is the whole point of recording failures as rows.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).with_name('progress.jsonl')


def load():
    return [json.loads(line) for line in SRC.read_text().splitlines() if line.strip()]


def split_runs(rows):
    """A run ends where the inbox counter falls back to a smaller value."""
    runs, cur, prev = [], [], None
    for r in rows:
        c = r.get('inbox', {}).get('count')
        if c is not None:
            if prev is not None and c < prev:
                runs.append(cur)
                cur = []
            prev = c
        cur.append(r)
    runs.append(cur)
    return runs


def ts(r):
    return datetime.fromisoformat(r['ts'])


def fmt_dur(seconds):
    h, rem = divmod(int(seconds), 3600)
    return f'{h}h {rem // 60:02d}m {rem % 60:02d}s'


def phase0(run):
    """Inbox backfill: invisible to dipdup_index, so rollup_inbox_message is the only signal."""
    pts = [r for r in run if r.get('inbox') and not r.get('indexes')]
    if not pts:
        return
    t0 = ts(pts[0])
    print(f'\n## Phase 0 — inbox backfill inside on_restart  (t0 {t0.isoformat()})\n')
    print('| UTC | elapsed | rows | max(level) | Δrows | Δlevel | lvl/s | pending |')
    print('|---|---|--:|--:|--:|--:|--:|--:|')
    prev = None
    for r in pts:
        t, ib = ts(r), r['inbox']
        lvl = ib.get('max_level')
        pend = r.get('pending_outbox_levels')
        pend = len(pend) if isinstance(pend, list) else ('-' if pend is None else pend)
        d_rows = d_lvl = rate = ''
        if prev and lvl and prev[2]:
            dt = (t - prev[0]).total_seconds()
            d_rows = ib['count'] - prev[1]
            d_lvl = lvl - prev[2]
            rate = f'{d_lvl / dt:,.1f}' if dt else ''
        print(
            f'| {t:%m-%d %H:%M:%S} | {fmt_dur((t - t0).total_seconds())} | {ib["count"]:,} '
            f'| {lvl:,} | {d_rows} | {d_lvl if d_lvl == "" else f"{d_lvl:,}"} | {rate} | {pend} |'
        )
        prev = (t, ib['count'], lvl)
    span = (ts(pts[-1]) - t0).total_seconds()
    first, last = pts[0]['inbox'].get('max_level'), pts[-1]['inbox'].get('max_level')
    if span and first and last and last > first:
        print(f'\nPhase-0 so far: {fmt_dur(span)}, {last - first:,} levels, **average {(last - first) / span:,.1f} lvl/s**')
        print('(June baseline: 5 h 18.5 m, 8.14M levels, ~425 lvl/s)')


def phases12(run):
    pts = [r for r in run if r.get('indexes')]
    if not pts:
        print('\n## Phases 1-2 — not started (dipdup_index still empty)')
        return
    t0 = ts(pts[0])
    names = sorted({n for r in pts for n in r['indexes']})
    print(f'\n## Phases 1-2 — {len(names)} indexes, spawned {t0.isoformat()}\n')
    print('| UTC | ' + ' | '.join(n.replace('etherlink_', 'eth_').replace('_operations', '_ops') for n in names) + ' |')
    print('|---' * (len(names) + 1) + '|')
    for r in pts:
        cells = []
        for n in names:
            i = r['indexes'].get(n)
            if not i:
                cells.append('-')
            else:
                cells.append(('**rt** ' if i['status'] == 'realtime' else '') + f'{i["level"]:,}')
        print(f'| {ts(r):%m-%d %H:%M} | ' + ' | '.join(cells) + ' |')
    print('\n### Rates (lvl/min, over the whole window so far)\n')
    print('| index | first | last | Δ | lvl/min | status |')
    print('|---|--:|--:|--:|--:|---|')
    span = (ts(pts[-1]) - t0).total_seconds() / 60
    for n in names:
        f = next((r['indexes'][n] for r in pts if n in r['indexes']), None)
        last = next((r['indexes'][n] for r in reversed(pts) if n in r['indexes']), None)
        if not f or not last:
            continue
        d = last['level'] - f['level']
        print(f'| {n} | {f["level"]:,} | {last["level"]:,} | {d:,} | {d / span:,.0f} | {last["status"]} |' if span else '')


def main():
    rows = load()
    runs = split_runs(rows)
    run = runs[-1]
    print(f'# Staging reindex — {len(rows)} samples, {len(runs)} run(s); measuring the last')
    errs = [r for r in run if 'error' in r]
    if errs:
        print(f'\n{len(errs)} error sample(s) in this run; last: {errs[-1]["ts"]} {errs[-1]["error"]}')
    phase0(run)
    phases12(run)
    print(f'\nLast sample: {run[-1]["ts"]}')


if __name__ == '__main__':
    sys.exit(main())
