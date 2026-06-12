#!/usr/bin/env python3
"""Verdict for the alias-registry case: did all 3 previewnet Initialized events land in l2_account?"""

from __future__ import annotations

from tests.stand import verify_lib as lib

# Golden vectors: every Initialized(string,bytes,uint256) event in previewnet history
# (verified live via eth_getLogs + eth_getTransactionByHash on 2026-06-11).
# alias address (40-hex, no 0x) -> (native_tz_address, initialized_at_level, initialized_at_tx)
GOLDEN = {
    '9adffdb184eae59204995cadfb57aafecc715d73': (
        'KT19iqeZxnYDz6GbU67uwF4ECH7bY46WTV2v',  # KT1 native source: empty pubkey in the event
        172338,
        '61a1e6ec1e9092d662aaada1756298b81778c3b93068ce673801b9c2d826f499',
    ),
    '3f2e3819832be16420810dfafc813334698bfa65': (
        'tz1ebDT2XwpwP6ja3ESuLUy8BgZwrmi1XpX3',
        172570,
        '0068f92ffeb3702939eecfd78187253295946f8afaa17cceb6abdf70f015c6e1',
    ),
    '93ee79eb06a7052f3d7f9c160b7927dd69ff4390': (
        'tz1LJmf4GUTrNsZVWXomSfqyWEWdNPo75Wz3',
        176345,
        '2fdc8fdc6aeceb2ee379694ec353b0205bd27a621755f3a0949f9bd9b6811c34',
    ),
}


def main() -> int:
    conn = lib.open_db('/tmp/bridge_alias_registry.sqlite')
    cur = conn.cursor()

    lib.section('Row counts')
    c = lib.count(cur, 'l2_account')
    print(f'  {"l2_account":<22} {"(missing)" if c < 0 else c}')

    lib.section('L2 accounts (l2_account) — the alias registry')
    accounts = {
        r['address']: r
        for r in lib.rows(
            cur,
            'SELECT address, kind, native_tz_address, initialized_at_level, initialized_at_tx '
            'FROM l2_account ORDER BY initialized_at_level LIMIT 20',
        )
    }
    for r in accounts.values():
        print(f'  {r["address"]} kind={r["kind"]} native={r["native_tz_address"]} lvl={r["initialized_at_level"]}')

    v = lib.Verdict()
    v.check(c == 3, 'exactly 3 l2_account rows (all Initialized events in previewnet history)')
    for alias, (native, level, tx) in GOLDEN.items():
        row = accounts.get(alias)
        tag = native[:3] if native.startswith('tz') else 'KT1'
        v.check(row is not None, f'alias {alias[:8]}… registered')
        if row is None:
            continue
        v.check(row['kind'] == 'evm_alias', f'alias {alias[:8]}… kind=evm_alias')
        v.check(row['native_tz_address'] == native, f'alias {alias[:8]}… native_tz_address is the {tag} source')
        v.check(row['initialized_at_level'] == level, f'alias {alias[:8]}… initialized_at_level={level}')
        v.check(row['initialized_at_tx'] == tx, f'alias {alias[:8]}… initialized_at_tx matches')
    conn.close()
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
