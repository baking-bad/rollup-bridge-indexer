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
    ('1991000000000000', 'xtz_evm', 'XTZ #1'),
    ('1997', FA_TOKEN, 'FA'),
    ('500000000000000000', 'xtz_evm', 'XTZ #2'),
]


def main() -> int:
    cur = lib.open_db('/tmp/bridge_alias_deposit.sqlite').cursor()
    lib.counts(cur, 'l2_account', 'l2_deposit')
    accounts = lib.dump_accounts(cur)
    deposits = lib.dump(cur, 'l2_deposit', 'level, l2_account_id, amount, token_id', order_by='level')
    seen = {(r['amount'], r['token_id']) for r in deposits}

    # Independent regression (NOT alias-related): this case happens to index an FA deposit, so it
    # also guards that the FA L2 proxy token inherits L1 metadata via register_etherlink_token
    # (model defaults are symbol null / decimals 0). It lives here only because this is the sole
    # stand case exercising the FA Deposit path.
    fa_token = lib.dump(cur, 'etherlink_token', 'id, symbol, decimals')
    fa = next((r for r in fa_token if r['id'] == FA_TOKEN), None)

    v = lib.Verdict()
    v.check(len(deposits) >= 3, 'three deposits (2 XTZ + 1 FA) indexed')
    v.check_alias(accounts, ALIAS, NATIVE)
    v.check(bool(deposits) and all(r['l2_account_id'] == ALIAS for r in deposits), 'every deposit keyed on the alias runtime address')
    for amount, token, label in EXPECTED:
        v.check((amount, token) in seen, f'deposit present: {label}')
    v.check(fa is not None and fa['decimals'] == 6, 'FA L2 token decimals mirror L1 (6, not default 0)')
    v.check(fa is not None and fa['symbol'] is not None, 'FA L2 token symbol populated from L1 (not null)')
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
