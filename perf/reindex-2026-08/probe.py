#!/usr/bin/env python3
"""Sample staging's indexing progress into a JSONL file, one object per probe.

Runs unattended for the length of a full reindex, so it must never die: every
probe is wrapped, and a failed one is recorded as a row with an `error` key
rather than ending the series. A gap in the series would be indistinguishable
from a stalled indexer, which is exactly the thing being measured.
"""
import json
import sys
import time
import urllib.request
from datetime import UTC
from datetime import datetime
from pathlib import Path

ENDPOINT = 'https://etherlink-bridge-mainnet-staging.dipdup.net/v1/graphql'
OUT = Path(__file__).with_name('progress.jsonl')
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 300

# Phase 0 is invisible to dipdup_index — the rollup inbox backfill runs inside
# on_restart, before any index row exists. rollup_inbox_message is the only
# signal there, so it is asked for on every probe, not just once indexes appear.
QUERY = '''{
  rollup_inbox_message_aggregate { aggregate { count max { level } } }
  rollup_outbox_message_aggregate { aggregate { count max { level } } }
  bridge_operation_aggregate { aggregate { count } }
  l1_deposit_aggregate { aggregate { count } }
  l1_withdrawal_aggregate { aggregate { count } }
  dipdup_index { name level status created_at updated_at }
  dipdup_head { name level created_at updated_at }
  dipdup_meta(where: {key: {_eq: "rollup_message_pending_outbox_levels"}}) { key value }
}'''


def probe() -> dict:
    body = json.dumps({'query': QUERY}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    if 'errors' in payload:
        return {'error': 'graphql', 'detail': payload['errors']}
    d = payload['data']

    def agg(key):
        a = d[key]['aggregate']
        out = {'count': a['count']}
        if a.get('max'):
            out['max_level'] = a['max']['level']
        return out

    return {
        'inbox': agg('rollup_inbox_message_aggregate'),
        'outbox': agg('rollup_outbox_message_aggregate'),
        'bridge_operation': agg('bridge_operation_aggregate')['count'],
        'l1_deposit': agg('l1_deposit_aggregate')['count'],
        'l1_withdrawal': agg('l1_withdrawal_aggregate')['count'],
        'indexes': {i['name']: {'level': i['level'], 'status': i['status'], 'updated_at': i['updated_at']} for i in d['dipdup_index']},
        'heads': {h['name']: {'level': h['level'], 'updated_at': h['updated_at']} for h in d['dipdup_head']},
        # The wipe detector in rollup_message.py drops this key when the inbox table comes up
        # empty. dipdup_meta outlives a schema wipe, so watching the key is how we see that
        # branch actually fire instead of assuming it did.
        'pending_outbox_levels': (d['dipdup_meta'][0]['value'] if d['dipdup_meta'] else None),
    }


while True:
    row = {'ts': datetime.now(UTC).isoformat(timespec='seconds')}
    try:
        row.update(probe())
    except Exception as e:
        row['error'] = type(e).__name__
        row['detail'] = str(e)[:300]
    with OUT.open('a') as f:
        f.write(json.dumps(row) + '\n')
    time.sleep(INTERVAL)
