# mainnet prod — deposit re-stitch repair

Fix the 984 corrupted `bridge_deposit` matches on **PROD** = `mainnet-staging` stack =
`etherlink-bridge-mainnet.dipdup.net`. Direct SQL re-stitch (no restart). Paste each file
into the **Hasura console → Data → SQL** tab (admin). Do NOT add `BEGIN;/COMMIT;` —
`run_sql` already wraps in one transaction. Every script is idempotent (safe to re-run).

**Freeze prod's image first** (pin the stack to its current pre-split digest) so a redeploy
can't pull split-`master`. Only then run the repair.

Order:
1. `backup_before.sql` — checkpoint (990 rows → `repair_backup_bd`/`repair_backup_bo`). Expect `backed_up_bd=990`.
2. `verify_repair.sql` — baseline. Expect `still_wrong≈984`, `resolved_clean≈0`.
3. `prod_repair_canary19.sql` — canary (19 whale rows). Expect `resolved_clean=19, still_wrong=0`. Spot-check in GraphQL (L1 acct == L2 acct, L2 ts ≥ L1 ts).
4. `prod_repair_full.sql` — all 984 (fixes 978, leaves 6 boundary pending). Expect `resolved_clean=978, still_wrong=0, boundary_pending=6`.
5. `verify_repair.sql` again — expect `still_wrong=0`. Then `verify_prod_only.sql` — expect `time_impossible_completed=0`.
6. `prod_repair_boundary6_cascade.sql` — OPTIONAL, after reviewing the 6 boundary rows. Brings them to candidate-consistency.

Rollback: `rollback_from_backup.sql` (restores from the snapshot tables).

Regenerate before applying if the whale kept depositing: re-run `audit2.py` (in the session
scratchpad) to refresh the corrupt set, then regenerate these from it.

2-phase note: `bridge_deposit.l2_transaction_id` is `unique` and the corrupt set is a near-closed
permutation, so each script NULLs all wrong L2s first, then sets the correct ones.
