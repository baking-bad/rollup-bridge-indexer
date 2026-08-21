-- =====================================================================================
-- CANARY: 19 whale (2026-06, time-impossible) corrupt rows ONLY.  Same engine as the full
-- script (2-phase, no BEGIN/COMMIT, idempotent). All 19 are 'clean' (correct L2 already free),
-- none are boundary. Apply FIRST, confirm VERIFICATION shows resolved_clean=19 still_wrong=0,
-- spot-check in GraphQL, THEN run prod_repair_full.sql for all 984.
-- =====================================================================================
CREATE TEMP TABLE _m (bd_id uuid PRIMARY KEY, wrong_l2_id uuid NOT NULL, correct_l2_id uuid NOT NULL, kind text NOT NULL) ON COMMIT DROP;
INSERT INTO _m (bd_id, wrong_l2_id, correct_l2_id, kind) VALUES
  ('0ad62694-eddd-416f-9e19-efb78d73575b','d28e0be4-10a2-4909-b0e1-b7b9d1e29912','3655201e-48ec-4819-874c-de321a1569bb','clean'),
  ('45944a24-4cb3-44aa-be98-ecb412192a96','ab088f00-d69e-4ce8-951c-e238f2f0897b','a217e94e-750e-4fb0-a4e2-c1e8908d2e4e','clean'),
  ('6a3b95da-89ff-41af-bb40-412f195dd270','7dabb262-ad29-4323-b13f-ff0bb70cc0a6','7daaec60-301b-4338-be85-cf82117b1ad0','clean'),
  ('6ec0b955-5171-4c85-865f-f0efff153b14','8108d819-eb36-4fe8-8fa1-4ee165ed99e9','c5a6a6e8-6c7f-4b31-9dbf-f467fc3c91aa','clean'),
  ('7170ef2d-a652-4c65-8f91-9f865edc0df8','c7b51574-550c-4392-8a69-a5ea4555d089','d1948433-9038-4ad1-93dd-c355da99af76','clean'),
  ('7d4b43f1-9e31-4505-b685-94ba6a9afaf8','991fbfcf-2097-4975-b7c4-d3f643bc26e0','f91ea3a0-d683-4b64-8863-9079c98ea392','clean'),
  ('7e4fc7b4-69ed-4ede-8712-3bf108a54aeb','e1f0386e-1271-4356-b14b-83ed49e0b123','79c9e692-32d5-4eb3-8abc-bf04b30ff5ad','clean'),
  ('8292b3b0-8ad4-4543-bf12-1682d1693fba','117f691a-f1dd-41d2-8a23-7f5dc4fd763f','ae3324f5-528f-49f9-998a-f5b2ae57fe2f','clean'),
  ('8c99d5cd-83f3-460d-ac1b-d25e48c22f48','ff039018-cd56-41a5-bc58-6e8bfd91eab7','0183855f-c898-4974-b18a-1741fed36cfb','clean'),
  ('a8af0aac-62f3-48d9-9905-2fd48cc7f1e3','b576ee64-a404-45d1-97ca-c497e0e7741b','99139231-80d5-43c6-8996-95c574be9ceb','clean'),
  ('b3e1f57e-fb2d-41c7-8147-e54815e9ed7c','3a6e03df-5720-47c6-b45c-4a6794aeb57b','71fe10d7-e5cd-4018-999d-12352c62ec41','clean'),
  ('b9b73267-b063-4111-86f1-62d696de12ae','671f270f-0b34-45b6-8ecc-f26088285501','2df95505-1939-4e8f-9683-a20a6db46ca8','clean'),
  ('c0c67180-d8e9-4aea-8d92-c35241e147b2','f2e26685-5fb0-4bd7-91c6-66d691a1deec','2c289173-651f-4d55-baff-51bf8552adcb','clean'),
  ('c84e9a68-15a5-4644-b3b2-62a5e669ff60','6ab28ca6-905b-459d-9e07-48da1d5e9c0a','f456a29f-ad97-4d5b-8705-d0835f517ac0','clean'),
  ('d17c4e29-7195-44d9-b00f-64ec496a7834','9a46f6cb-91ce-4385-83bb-84231c8927d9','414857b1-1964-452b-b29a-6a4ebd7c6255','clean'),
  ('d5394ed1-f2f2-4319-ba1f-f2cdce1eede5','380d90b5-2b31-417a-8e80-116aa420e3e9','2df3194a-39b8-434a-a60e-99913876171a','clean'),
  ('e18db0a3-87c3-4002-8ac1-53a99d8bc3d1','2b7c1cbc-e2ad-403e-b58a-d02f1b4b8957','2d8a995b-6092-4010-8983-8777f2190c23','clean'),
  ('e2b7a1cb-afd3-41f2-8a68-b2c8ebf61148','521b029c-92d4-4301-a428-a871a3b5a97a','951a7c2a-f2b6-4342-ba42-b2a5e1a12693','clean'),
  ('e8abd1fe-5de9-453a-80a0-b529227241f2','7f7cc2f7-f0c3-461d-9046-2524570725cc','7bad4982-8b49-46fe-8777-d5d7de1ed26a','clean');

-- (smoke) how many rows still hold their known-wrong L2 and will be reset by phase 1:
-- SELECT count(*) FROM bridge_deposit bd JOIN _m m ON m.bd_id=bd.id AND bd.l2_transaction_id=m.wrong_l2_id;

-- PHASE 1a: reset bridge_operation for every row still holding its wrong L2  -> CREATED
UPDATE bridge_operation bo
SET is_completed=false, is_successful=false, status='CREATED', updated_at=l1."timestamp"
FROM bridge_deposit bd
  JOIN _m m  ON m.bd_id=bd.id AND bd.l2_transaction_id=m.wrong_l2_id
  JOIN l1_deposit l1 ON l1.id=bd.l1_transaction_id
WHERE bo.id=bd.id;

-- PHASE 1b: free the wrong L2 (set NULL) for every row still holding it
UPDATE bridge_deposit bd
SET l2_transaction_id=NULL
FROM _m m
WHERE m.bd_id=bd.id AND bd.l2_transaction_id=m.wrong_l2_id;

-- PHASE 2a: assign the candidate-correct L2 to the cleanly-resolvable rows (now NULL/free)
UPDATE bridge_deposit bd
SET l2_transaction_id=m.correct_l2_id
FROM _m m
WHERE m.bd_id=bd.id AND m.kind='clean' AND bd.l2_transaction_id IS NULL;

-- PHASE 2b: mark those rows FINISHED/successful, updated_at = correct L2 timestamp
UPDATE bridge_operation bo
SET is_completed=true, is_successful=true, status='FINISHED', updated_at=ld."timestamp"
FROM bridge_deposit bd
  JOIN _m m ON m.bd_id=bd.id AND m.kind='clean'
  JOIN l2_deposit ld ON ld.id=m.correct_l2_id
WHERE bo.id=bd.id AND bd.l2_transaction_id=m.correct_l2_id;

-- VERIFICATION (returned result of the paste): must show resolved=<#clean>, still_wrong=0,
-- boundary_pending=<#boundary>. still_wrong MUST be 0.
SELECT
  (SELECT count(*) FROM bridge_deposit bd JOIN _m m ON m.bd_id=bd.id
     WHERE m.kind='clean' AND bd.l2_transaction_id=m.correct_l2_id)        AS resolved_clean,
  (SELECT count(*) FROM bridge_deposit bd JOIN _m m ON m.bd_id=bd.id
     WHERE bd.l2_transaction_id=m.wrong_l2_id)                             AS still_wrong,
  (SELECT count(*) FROM bridge_deposit bd JOIN _m m ON m.bd_id=bd.id
     WHERE m.kind='boundary' AND bd.l2_transaction_id IS NULL)            AS boundary_pending;
