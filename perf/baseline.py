#!/usr/bin/env python3
"""Throughput snapshot of a deployed indexer, for before/after comparison of a matcher patch.

Usage:  python3 perf/baseline.py [window_seconds] [--url URL] > perf/snapshots/<name>.json

The verdict is `derived.throughput_per_backlog_row`: useful rows written per minute, divided
by the backlog the matcher is obliged to walk. Why the division goes that way, what cannot be
measured with, and which patches this instrument can distinguish at all — see
docs/matcher-perf-patches.md, section "Как проверять, что стало лучше".

This measures a *deployed* instance over the network. For a controlled per-step measurement
against a seeded backlog, use `make bench-matcher` (tests/perf/) instead.

`derived.pool_shares` is each pool's share of the walked backlog — the expected signal size:
a patch fixing a pool worth 3% of the backlog is invisible from outside and needs the bench.
"""
import json
import subprocess
import sys
import time

URL = 'https://etherlink-bridge-mainnet-staging.dipdup.net/v1/graphql'

Q_INDEX = '{ dipdup_index(order_by:{name:asc}) { name status level updated_at } }'

Q_COUNTS = '''{
 l1d: l1_deposit_aggregate { aggregate { count } }
 l2d: l2_deposit_aggregate { aggregate { count } }
 l1w: l1_withdrawal_aggregate { aggregate { count } }
 l2w: l2_withdrawal_aggregate { aggregate { count } }
 bd: bridge_deposit_aggregate { aggregate { count } }
 bw: bridge_withdrawal_aggregate { aggregate { count } }
 bo: bridge_operation_aggregate { aggregate { count } }
 la: l2_account_aggregate { aggregate { count } }
 inbox: rollup_inbox_message_aggregate { aggregate { count } }
 outbox: rollup_outbox_message_aggregate { aggregate { count } }
}'''

# The pools the matcher walks (the outer side), against the pools it queries into once per
# walked row (the inner side). Pass cost = the size of the OUTER pool; a patch's win is
# moving the work onto the inner one.
Q_POOLS = '''{
 outer_tezos_withdrawals: l1_withdrawal_aggregate(where:{_not:{bridge_withdrawals:{}}, outbox_message:{builder:{_eq:"kernel"}}}) { aggregate { count } }
 outer_claimed_fast: l1_withdrawal_aggregate(where:{_not:{bridge_withdrawals:{}}, outbox_message:{builder:{_eq:"service_provider"}, parameters_hash:{_is_null:false}}}) { aggregate { count } }
 outer_xtz_deposits: l2_deposit_aggregate(where:{_not:{bridge_deposits:{}}, token_id:{_eq:"xtz_evm"}, runtime_kind:{_eq:"evm"}}) { aggregate { count } }
 outer_etherlink_deposits: l2_deposit_aggregate(where:{_not:{bridge_deposits:{}}, inbox_message_level:{_is_null:false}}) { aggregate { count } }
 outer_michelson_deposits: l2_deposit_aggregate(where:{_not:{bridge_deposits:{}}, inbox_message_level:{_is_null:true}, runtime_kind:{_eq:"michelson"}}) { aggregate { count } }
 outer_etherlink_withdrawals: l2_withdrawal_aggregate(where:{_not:{bridge_withdrawals:{}}}) { aggregate { count } }
 outer_tezos_deposits: l1_deposit_aggregate(where:{_not:{bridge_deposits:{}}}) { aggregate { count } }
 outer_inbox: bridge_deposit_aggregate(where:{inbox_message_id:{_is_null:true}}) { aggregate { count } }
 outer_outbox: bridge_withdrawal_aggregate(where:{outbox_message_id:{_is_null:true}}) { aggregate { count } }
 inner_bridge_withdrawals: bridge_withdrawal_aggregate(where:{l1_transaction_id:{_is_null:true}}) { aggregate { count } }
 inner_bridge_deposits: bridge_deposit_aggregate(where:{l2_transaction_id:{_is_null:true}}) { aggregate { count } }
}'''


def gq(url: str, query: str) -> dict:
    r = subprocess.run(
        ['curl', '-s', '-H', 'content-type: application/json', '-d', json.dumps({'query': query}), url],
        capture_output=True,
        text=True,
    )
    d = json.loads(r.stdout)
    if 'errors' in d:
        raise SystemExit(f'GraphQL rejected the whole query: {d["errors"]}')
    return d['data']


def flatten(d: dict) -> dict:
    return {k: v['aggregate']['count'] for k, v in d.items()}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    url = URL
    if '--url' in sys.argv:
        url = sys.argv[sys.argv.index('--url') + 1]
    window = float(args[0]) if args else 300.0

    t0 = time.time()
    idx0 = {x['name']: x for x in gq(url, Q_INDEX)['dipdup_index']}
    cnt0 = flatten(gq(url, Q_COUNTS))
    pools0 = flatten(gq(url, Q_POOLS))

    time.sleep(window)

    t1 = time.time()
    idx1 = {x['name']: x for x in gq(url, Q_INDEX)['dipdup_index']}
    cnt1 = flatten(gq(url, Q_COUNTS))
    pools1 = flatten(gq(url, Q_POOLS))
    minutes = (t1 - t0) / 60

    levels = {
        n: {'level': idx1[n]['level'], 'status': idx1[n]['status'], 'per_min': (idx1[n]['level'] - idx0[n]['level']) / minutes}
        for n in idx1
    }
    rows = {k: {'count': cnt1[k], 'per_min': (cnt1[k] - cnt0[k]) / minutes} for k in cnt1}

    aggregate_levels = sum(v['per_min'] for v in levels.values())
    rows_per_min = sum(rows[k]['per_min'] for k in ('l1d', 'l2d', 'l1w', 'l2w', 'bo'))
    outer_total = sum(v for k, v in pools1.items() if k.startswith('outer_'))
    inner_total = sum(v for k, v in pools1.items() if k.startswith('inner_'))

    snapshot = {
        'taken_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t1)),
        'window_min': round(minutes, 2),
        'url': url,
        'indexes': levels,
        'rows': rows,
        'pools_start': pools0,
        'pools_end': pools1,
        'derived': {
            'aggregate_levels_per_min': round(aggregate_levels),
            'rows_per_min': round(rows_per_min, 1),
            'outer_pool_total': outer_total,
            'inner_pool_total': inner_total,
            # THE VERDICT IS READ HERE. Useful rows per minute per row of backlog the matcher
            # walks. Higher is better. A patch must raise this AND do so at a backlog no
            # smaller than the baseline's: if outer_pool_total fell, the backlog explains the
            # rise, not the patch.
            'throughput_per_backlog_row': round(rows_per_min / outer_total, 6) if outer_total else None,
            # Each pool's share of the walked backlog = the expected signal size from a patch
            # fixing that pool. Below a few percent it is not distinguishable from outside.
            'pool_shares': (
                {
                    k[len('outer_') :]: round(v / outer_total, 3)
                    for k, v in sorted(pools1.items(), reverse=True)
                    if k.startswith('outer_') and v
                }
                if outer_total
                else {}
            ),
            # How many times fewer rows the matcher would walk if it went down the counter
            # side. An estimate of the ceiling, not a measurement.
            'inversion_factor': round(outer_total / inner_total, 1) if inner_total else None,
        },
    }
    print(json.dumps(snapshot, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
