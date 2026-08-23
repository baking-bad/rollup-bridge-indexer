#!/usr/bin/env python3
"""Emit one line per state change in the sampler's output. Silence means healthy.

Reads progress.jsonl rather than polling the endpoint again: the systemd probe is
the single source of truth, and a second poller would disagree with it at the
edges. Every terminal state emits -- a stall looks identical to a healthy drain
from the counters alone, so the stall branch is what covers "the process died".
"""
import json
import time
from pathlib import Path

JSONL = Path('/home/ubuntu/rollup-bridge-indexer/perf/reindex-2026-08/progress.jsonl')
STALL_S = 20 * 60          # counters frozen this long is anomalous even mid-drain
RENOTIFY_S = 60 * 60

def key(r):
    """The tuple that must move while the indexer is working."""
    return (
        r.get('inbox', {}).get('max_level'),
        r.get('inbox', {}).get('count'),
        r.get('outbox', {}).get('count'),
        tuple(sorted((n, i['level']) for n, i in r.get('indexes', {}).items())),
    )

with JSONL.open() as f:
    f.seek(0, 2)                       # only new rows -- history is already read
    last_key, last_move, stalled_at = None, time.time(), None
    idx_seen, rt_seen, err_run = set(), set(), 0
    while True:
        line = f.readline()
        if not line:
            if stalled_at and time.time() - last_move > STALL_S:
                if time.time() - stalled_at > RENOTIFY_S:
                    stalled_at = time.time()
                    print(f'STILL STALLED {int((time.time()-last_move)//60)}m with no counter movement', flush=True)
            time.sleep(5)
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue

        if 'error' in r:
            err_run += 1
            if err_run == 3:
                print(f"PROBE ERRORS x3: {r['error']} {r.get('detail','')[:120]}", flush=True)
            continue
        err_run = 0

        k = key(r)
        now = time.time()
        if k != last_key:
            if stalled_at:
                print(f"RECOVERED after {int((now-last_move)//60)}m -- max_level={k[0]} outbox={k[2]}", flush=True)
                stalled_at = None
            last_key, last_move = k, now
        elif not stalled_at and now - last_move > STALL_S:
            stalled_at = now
            print(f"STALLED {int((now-last_move)//60)}m: max_level={k[0]} inbox={k[1]} outbox={k[2]} idx={len(r.get('indexes',{}))}", flush=True)

        for name, i in r.get('indexes', {}).items():
            if name not in idx_seen:
                idx_seen.add(name)
                print(f"INDEX APPEARED {name} level={i['level']} status={i['status']} (phase 0 over for this index)", flush=True)
            if i['status'] == 'realtime' and name not in rt_seen:
                rt_seen.add(name)
                print(f"REALTIME {name} level={i['level']} ({len(rt_seen)}/{len(idx_seen)} indexes)", flush=True)
