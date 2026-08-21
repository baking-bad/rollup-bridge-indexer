-- =====================================================================================
-- ROLLBACK from the snapshot taken by backup_before.sql. Restores l2_transaction_id and the
-- bridge_operation flags to their pre-repair values for all 990 affected rows. Run in Hasura SQL.
-- Safe to re-run. Does NOT drop the backup tables (drop them manually when satisfied).
-- =====================================================================================
UPDATE bridge_deposit bd SET l2_transaction_id = b.l2_transaction_id
FROM repair_backup_bd b WHERE b.id = bd.id
  AND bd.l2_transaction_id IS DISTINCT FROM b.l2_transaction_id;
UPDATE bridge_operation bo
SET is_completed=b.is_completed, is_successful=b.is_successful, status=b.status, updated_at=b.updated_at
FROM repair_backup_bo b WHERE b.id = bo.id;
SELECT count(*) AS restored FROM repair_backup_bd;
