#!/usr/bin/env python3
"""alias-deposit verdict: deposits TO an EVM alias resolve the receiver to its tz origin.

See README.md for the on-chain sample; window.env for the block window.
"""

from __future__ import annotations

from tests.stand import verify_lib as lib

ALIAS = '21ab829afe4b41dd3fb6eb5be4de2d20d9cebc21'
NATIVE = 'tz1ekkzEN2LB1cpf7dCaonKt6x9KVd9YVydc'
FA_TOKEN = '3dff505a2a69e6e0b05fdb71b5f6ddd514fdaf47'

# Each deposit the handlers index in this window: (amount, token_id, label).
EXPECTED = [
    ('1991000000000000', 'xtz_evm', 'XTZ before init'),
    ('1997', FA_TOKEN, 'FA before init'),
    ('500000000000000000', 'xtz_evm', 'XTZ after init'),
]


def main() -> int:
    cur = lib.open_db('/tmp/bridge_alias_deposit.sqlite').cursor()
    lib.counts(cur, 'l2_account', 'l2_deposit')
    accounts = lib.dump_accounts(cur)
    deposits = lib.dump(cur, 'l2_deposit', 'level, l2_account, amount, token_id', order_by='level')
    seen = {(r['amount'], r['token_id']) for r in deposits}

    v = lib.Verdict()
    v.check(len(deposits) >= 3, 'XTZ (before + after init) + FA (before) deposits indexed')
    v.check_alias(accounts, ALIAS, NATIVE)
    v.check(bool(deposits) and all(r['l2_account'] == NATIVE for r in deposits), 'every deposit attributed to tz origin')
    for amount, token, label in EXPECTED:
        v.check((amount, token) in seen, f'deposit present: {label}')
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
