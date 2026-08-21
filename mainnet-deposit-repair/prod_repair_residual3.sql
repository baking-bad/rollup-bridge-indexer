-- =====================================================================================
-- prod_repair_residual3.sql
-- Run AFTER prod_repair_full.sql + prod_repair_boundary6_cascade.sql.
--
-- Cleans the FINAL 3 residual FA-bridge mis-stitches that verify_prod_only.sql still
-- flags (account-mismatch list). Each is a real FA-token deposit (ticketer KT1Wj8SU...,
-- rollup sr1Ghq66...) that the matcher wrongly stitched onto an unrelated XTZ L2 deposit
-- (different account, XTZ-shaped 1e18 amount). The CANDIDATE leaves all 3 FA L1 deposits
-- UNMATCHED, and 2 of the stolen XTZ L2s belong to a same-account XTZ L1 the candidate
-- DOES match. So: null the 3 FA L2 links (-> CREATED/pending = candidate), then re-home
-- the 2 freed XTZ legs to their true XTZ owners (-> FINISHED). The 3rd freed L2
-- (6d5823...) has no candidate owner and is left free.
--
-- Hasura run_sql safe: NO BEGIN/COMMIT (run_sql wraps in one txn). Idempotent: every
-- UPDATE is guarded on the row still holding its known pre-repair value. 2-phase to dodge
-- the UNIQUE(bridge_deposit.l2_transaction_id) constraint: free all 3 FA links first,
-- then assign the freed ids. All ids are live-resolved prod UUIDs.
--
-- Mapping (verified against prod + candidate-staging + mainnet TzKT):
--   FA bd_id                                wrong/stolen L2 (l2_deposit id)         true XTZ owner bd_id (l1 op)
--   0a9aaff7 (oobxEmWA, recv 7a89b203)  ->  d3b47950 (6d5823.. 0.1 XTZ)          -> none (candidate unmatched)
--   9fd36d7f (opG65UL,  recv 9e391338)  ->  17189a23 (45e394.. 20 XTZ)           -> 32793f96 (ooScULeC, acct 4e754f29)
--   29081440 (oni9nqc,  recv 7c059fd8)  ->  132d6dbd (2deae7.. 1.9 XTZ)          -> 5322d120 (onz3cqGE, acct 2dc6ac56)
-- =====================================================================================

CREATE TEMP TABLE _fa (bd_id uuid PRIMARY KEY, wrong_l2_id uuid NOT NULL) ON COMMIT DROP;
INSERT INTO _fa VALUES
  ('0a9aaff7-245d-4621-8759-bd06f5620f5f','d3b47950-778b-4ad8-8152-b480cc78b8f7'),
  ('9fd36d7f-1745-4bea-97be-ed65237debd5','17189a23-eaa8-474a-9c20-d3f8941bc570'),
  ('29081440-c460-44b5-9d73-02de7988d8c3','132d6dbd-93ab-4d4d-850d-17ae593a5ed8');

CREATE TEMP TABLE _own (owner_bd uuid PRIMARY KEY, l2_id uuid NOT NULL) ON COMMIT DROP;
INSERT INTO _own VALUES
  ('32793f96-55fa-476c-b1e6-f17fd4cdecde','17189a23-eaa8-474a-9c20-d3f8941bc570'),
  ('5322d120-d0b4-4efd-969c-856408a65273','132d6dbd-93ab-4d4d-850d-17ae593a5ed8');

-- ---------- Phase 1: free the 3 FA holders (-> CREATED/pending) ----------
-- bridge_operation first (its guard reads the still-present wrong L2 link), then null the link.
UPDATE bridge_operation bo
   SET is_completed=false, is_successful=false, status='CREATED', updated_at=l1."timestamp"
  FROM bridge_deposit bd
  JOIN _fa f       ON f.bd_id=bd.id
  JOIN l1_deposit l1 ON l1.id=bd.l1_transaction_id
 WHERE bo.id=bd.id AND bd.l2_transaction_id=f.wrong_l2_id;

UPDATE bridge_deposit bd
   SET l2_transaction_id=NULL
  FROM _fa f
 WHERE f.bd_id=bd.id AND bd.l2_transaction_id=f.wrong_l2_id;

-- ---------- Phase 2: re-home the 2 freed XTZ legs to true owners (-> FINISHED) ----------
-- assign only if owner still pending AND the target L2 is now actually free (constraint-safe).
UPDATE bridge_deposit bd
   SET l2_transaction_id=o.l2_id
  FROM _own o
 WHERE o.owner_bd=bd.id
   AND bd.l2_transaction_id IS NULL
   AND NOT EXISTS (SELECT 1 FROM bridge_deposit x WHERE x.l2_transaction_id=o.l2_id);

UPDATE bridge_operation bo
   SET is_completed=true, is_successful=true, status='FINISHED', updated_at=ld."timestamp"
  FROM bridge_deposit bd
  JOIN _own o      ON o.owner_bd=bd.id
  JOIN l2_deposit ld ON ld.id=o.l2_id
 WHERE bo.id=bd.id AND bd.l2_transaction_id=o.l2_id;

-- ---------- Verification (expect: 3, 2, 3, 2) ----------
SELECT
  (SELECT count(*) FROM bridge_deposit bd JOIN _fa f  ON f.bd_id=bd.id    WHERE bd.l2_transaction_id IS NULL)         AS fa_now_pending,
  (SELECT count(*) FROM bridge_deposit bd JOIN _own o ON o.owner_bd=bd.id WHERE bd.l2_transaction_id=o.l2_id)         AS xtz_owners_assigned,
  (SELECT count(*) FROM bridge_operation bo JOIN _fa f  ON f.bd_id=bo.id    WHERE bo.status='CREATED')                AS fa_ops_created,
  (SELECT count(*) FROM bridge_operation bo JOIN _own o ON o.owner_bd=bo.id WHERE bo.status='FINISHED')               AS xtz_ops_finished;
