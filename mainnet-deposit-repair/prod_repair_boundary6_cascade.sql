-- =====================================================================================
-- OPTIONAL boundary cascade -- run AFTER prod_repair_full.sql, only once you accept the
-- evidence below. Each of these 6 corrupt rows' correct L2 is currently held by a NON-corrupt
-- bridge_deposit whose L1 op the CANDIDATE leaves PENDING (i.e. a prod-side mismatch / part of
-- the flagged prod-only anomaly set). This frees those 6 holders (-> CREATED/pending, matching
-- the candidate) and assigns each boundary row its correct L2. No BEGIN/COMMIT (run_sql wraps).
-- Mapping: (corrupt_bd, correct_l2_id, holder_bd_to_free)
-- =====================================================================================
CREATE TEMP TABLE _b (bd_id uuid PRIMARY KEY, correct_l2_id uuid NOT NULL, holder_bd uuid NOT NULL) ON COMMIT DROP;
INSERT INTO _b VALUES
  ('3456ab2a-d027-4ea6-b5ca-3aa2066ffafd','9c418a72-a13d-4539-899b-d82f56691cfc','527a1ac8-9e87-4e2c-b405-ff01b06f8e77'),
  ('40c7acee-e238-4ce9-af6b-783c9a89cf61','aea5f9e3-8135-469d-94bb-60e6ddcd14b1','e600c45c-1be6-4232-b8f8-2e5dc9c3bdde'),
  ('67efb39e-0143-462a-b449-753ad0894bdd','625cab0f-680d-4886-97f4-627a3e3a99e7','a64787b2-26b8-425d-a83a-9fd8a487eb1d'),
  ('89a16c69-a94d-4459-bdca-edbc63fc8bff','d259d307-f165-4444-8d7c-41205d42519d','fce27c12-1304-4128-8f31-588388429410'),
  ('8c6ad101-6353-4e21-be2b-986029786dd1','63797ce6-b3c2-4573-b3c5-f0b82a5e72e0','90bbd4a6-8227-473e-a1e5-160fa8e4023f'),
  ('ebf3df16-e49b-424c-9add-1117e1decbee','b1500b36-1c6f-4420-8ddc-bc02b297983a','64a29637-5140-4c0d-bdf6-2b01e4929ca3');

-- free the 6 holders (-> pending/CREATED)
UPDATE bridge_operation bo SET is_completed=false,is_successful=false,status='CREATED',updated_at=l1."timestamp"
FROM bridge_deposit bd JOIN _b b ON b.holder_bd=bd.id JOIN l1_deposit l1 ON l1.id=bd.l1_transaction_id
WHERE bo.id=bd.id;
UPDATE bridge_deposit bd SET l2_transaction_id=NULL FROM _b b WHERE b.holder_bd=bd.id;

-- assign the 6 boundary rows their correct L2 (now free) + mark FINISHED
UPDATE bridge_deposit bd SET l2_transaction_id=b.correct_l2_id
FROM _b b WHERE b.bd_id=bd.id AND bd.l2_transaction_id IS NULL;
UPDATE bridge_operation bo SET is_completed=true,is_successful=true,status='FINISHED',updated_at=ld."timestamp"
FROM bridge_deposit bd JOIN _b b ON b.bd_id=bd.id JOIN l2_deposit ld ON ld.id=b.correct_l2_id
WHERE bo.id=bd.id AND bd.l2_transaction_id=b.correct_l2_id;

SELECT (SELECT count(*) FROM bridge_deposit bd JOIN _b b ON b.bd_id=bd.id WHERE bd.l2_transaction_id=b.correct_l2_id) AS boundary_fixed,
       (SELECT count(*) FROM bridge_deposit bd JOIN _b b ON b.holder_bd=bd.id WHERE bd.l2_transaction_id IS NULL) AS holders_freed;
