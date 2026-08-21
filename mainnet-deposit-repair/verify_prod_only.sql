-- =====================================================================================
-- PROD-ONLY smoke checks (no candidate needed). Heuristics; authoritative oracle is audit2.py
-- vs the candidate DB. Run in Hasura -> Data -> SQL.
-- =====================================================================================

-- (1) TIME-IMPOSSIBLE completed deposits: L2 timestamp strictly before its L1 timestamp.
--     This cleanly catches the 19 whale rows. MUST be 0 after repair.
SELECT count(*) AS time_impossible_completed
FROM bridge_deposit bd
  JOIN l1_deposit l1 ON l1.id=bd.l1_transaction_id
  JOIN l2_deposit l2 ON l2.id=bd.l2_transaction_id
WHERE bd.l2_transaction_id IS NOT NULL AND l2."timestamp" < l1."timestamp";

-- (2) ACCOUNT-MISMATCH completed deposits: L2 beneficiary != L1 declared l2_account.
--     Catches ~974/984 of the corruption. NOTE: a small number of legitimate FA/proxy deposits
--     can also differ here, so treat a non-zero residual as a list to eyeball, not proof.
--     List them:
SELECT bd.id AS bridge_deposit_id, l1.operation_hash, l1.l2_account AS l1_acct,
       l2.l2_account AS l2_acct, l1."timestamp" AS l1_ts, l2."timestamp" AS l2_ts
FROM bridge_deposit bd
  JOIN l1_deposit l1 ON l1.id=bd.l1_transaction_id
  JOIN l2_deposit l2 ON l2.id=bd.l2_transaction_id
WHERE bd.l2_transaction_id IS NOT NULL AND l1.l2_account <> l2.l2_account
ORDER BY l1."timestamp";
