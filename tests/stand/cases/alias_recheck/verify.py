#!/usr/bin/env python3
"""alias-recheck verdict: the re-resolution cooldown branch runs without breaking the indexer.

Three XTZ deposits land on the same non-alias EVM receiver. With ALIAS_RECHECK_SECONDS=0
(window.env) every repeat sighting is past its cooldown, so the 2nd and 3rd deposits re-resolve
the account in place (an `originOf` call + UPDATE) instead of returning from cache. originOf is
queried at the node head, so the receiver stays a native account (kind=evm, its own origin) and
re-resolution must not duplicate the row or drop a deposit.

See README.md for the on-chain sample; window.env for the block window.
"""

from __future__ import annotations

from tests.stand import verify_lib as lib

RECEIVER = '7e6f6ccfe485a087f0f819eabfdbfb1a49b97677'

# Each XTZ deposit the handler indexes in this window: (amount, label).
EXPECTED = [
    ('62992000000000000', 'XTZ #1'),
    ('5432000000000000', 'XTZ #2'),
    ('83412000000000000', 'XTZ #3'),
]


def main() -> int:
    cur = lib.open_db('/tmp/bridge_alias_recheck.sqlite').cursor()
    lib.counts(cur, 'l2_account', 'l2_deposit')
    accounts = lib.dump_accounts(cur)
    deposits = lib.dump(cur, 'l2_deposit', 'level, l2_account_id, amount, token_id', order_by='level')
    seen = {(r['amount'], r['token_id']) for r in deposits}
    row = accounts.get(RECEIVER)

    v = lib.Verdict()
    v.check(len(deposits) >= 3, 'three XTZ deposits indexed (indexer survived the re-resolutions)')
    v.check(len(accounts) == 1, 're-resolution updated one row, did not duplicate it')
    v.check(row is not None and row['kind'] == 'evm', 'native receiver stays kind == evm')
    v.check(row is not None and row['origin'] == RECEIVER, 'native receiver is its own origin')
    v.check(bool(deposits) and all(r['l2_account_id'] == RECEIVER for r in deposits), 'every deposit keyed on the receiver')
    for amount, label in EXPECTED:
        v.check((amount, 'xtz_evm') in seen, f'deposit present: {label}')
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
