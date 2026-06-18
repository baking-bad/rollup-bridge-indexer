#!/usr/bin/env python3
"""alias-withdrawal verdict: withdrawals FROM an EVM alias resolve the sender to its tz origin.

See README.md for the on-chain sample; window.env for the block window.
"""

from __future__ import annotations

from tests.stand import verify_lib as lib

ALIAS = '21ab829afe4b41dd3fb6eb5be4de2d20d9cebc21'
NATIVE = 'tz1ekkzEN2LB1cpf7dCaonKt6x9KVd9YVydc'

# Each withdrawal the handlers index in this window: (amount, label). XTZ amount is the raw EVM 0.5e18.
EXPECTED = [
    ('500000000000000000', 'XTZ'),
    ('1', 'FA'),
]


def main() -> int:
    cur = lib.open_db('/tmp/bridge_alias_withdrawal.sqlite').cursor()
    lib.counts(cur, 'l2_account', 'l2_withdrawal')
    accounts = lib.dump_accounts(cur)
    withdrawals = lib.dump(cur, 'l2_withdrawal', 'level, l2_account_id, amount, kernel_withdrawal_id, l1_account', order_by='level')
    amounts = {r['amount'] for r in withdrawals}

    v = lib.Verdict()
    v.check(len(withdrawals) >= 2, 'XTZ + FA withdrawals indexed')
    v.check_alias(accounts, ALIAS, NATIVE)
    v.check(bool(withdrawals) and all(r['l2_account_id'] == ALIAS for r in withdrawals), 'every withdrawal keyed on the alias runtime address')
    for amount, label in EXPECTED:
        v.check(amount in amounts, f'withdrawal present: {label}')
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
