#!/usr/bin/env python3
"""alias-fast-withdrawal verdict: a fast XTZ withdrawal via NAC indexes on L2.

A native tz account triggers a fast XTZ withdrawal through a NAC; on the EVM runtime it surfaces as
a FastWithdrawal on 0xff…01 whose l2_caller is the account's EVM alias. on_xtz_withdraw must record
an l2_withdrawal with fast_payload set (the fast marker) and resolve the alias back to its tz origin.

See README.md for the on-chain sample; window.env for the block window.
"""

from __future__ import annotations

from tests.stand import verify_lib as lib

ALIAS = '21ab829afe4b41dd3fb6eb5be4de2d20d9cebc21'
NATIVE = 'tz1ekkzEN2LB1cpf7dCaonKt6x9KVd9YVydc'


def main() -> int:
    cur = lib.open_db('/tmp/bridge_alias_fast_withdrawal.sqlite').cursor()
    lib.counts(cur, 'l2_account', 'l2_withdrawal')
    accounts = lib.dump_accounts(cur)
    withdrawals = lib.dump(cur, 'l2_withdrawal', 'level, l2_account_id, amount, kernel_withdrawal_id, fast_payload', order_by='level')

    v = lib.Verdict()
    v.check(len(withdrawals) == 1, 'one fast withdrawal indexed')
    v.check_alias(accounts, ALIAS, NATIVE)
    w = withdrawals[0] if withdrawals else None
    v.check(w is not None and w['fast_payload'] is not None, 'fast withdrawal recorded with a fast_payload')
    v.check(w is not None and w['l2_account_id'] == ALIAS, 'withdrawal keyed on the alias runtime address')
    v.check(w is not None and w['kernel_withdrawal_id'] == 87, 'withdrawal_id == 87')
    v.check(w is not None and w['amount'] == '10000000000000000', 'amount matches the on-chain value')
    return v.report()


if __name__ == '__main__':
    raise SystemExit(main())
