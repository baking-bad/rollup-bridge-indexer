# Full-reindex / realtime / ingest perf — ground truth

Raw data extracted 2026-07-09 from Claude Code session transcripts (session ids in Sources).
All numbers below are **measured/observed**; deviations from earlier summaries are flagged.
Companion file: `STAGING_PERF_GROUNDTRUTH.md` (2026-06-05 node-change + datasource-headroom measurements).

## 1. Full mainnet reindex from scratch, 2026-06-29 01:01 → 2026-07-02 ~12:04 UTC

Stack: mainnet (was serving etherlink-bridge-mainnet-staging.dipdup.net), fresh master + new API (PR #38),
wipe + reindex. Host = the "good" Swarm node (post-06-05 move). Ranges: L1 7.0M→13.85M, L2 4.02M→46.7M (head at start).

**End-to-end: 3 d 11 h** (01:01:47 UTC 06-29 = first `rollup_inbox_message.created_at` → 07-02 12:04 = 6/9 indexes
realtime, remaining 3 evm-events 15 levels from head in the routine syncing/realtime flip).

### Phase 0 — inbox backfill inside on_restart (public API blind, `dipdup_index` empty)

01:01:47 → 06:20:20 = **5 h 18.5 m**. Probes = admin SQL `count(*), max(level) FROM rollup_inbox_message`:

| UTC 06-29 | rows | max(level) |
|---|--:|--:|
| 01:05:07 | 304 | 5,712,899 |
| 01:28:51 | 2,672 | 6,480,619 |
| 01:58:23 | 5,091 | 7,481,643 |
| 02:27:36 | 6,850 | 8,426,592 |
| 03:14:11 | 12,121 | 9,492,497 |
| 04:16:09 | 23,447 | 10,694,474 |
| 05:17:54 | 30,081 | 12,193,037 |
| 06:04:37 | 34,255 | 13,465,258 |
| 06:20:20 | 35,550 | 13,846,892 |

Pairwise rates: 512–597 lvl/s first hour, dips to 323–405 mid-phase; **phase average ≈ 425 lvl/s**
(~8.14M levels / 19,113 s). NB the earlier doc said "500–600 lvl/s" — that was first-hour only.
First probe already at 5.71M 3.3 min after start; exact scan start level not captured.
End of phase detected by public API coming alive (Hasura metadata tracked); no "syncing complete" log line captured.

### Phases 1–2 — 9 indexes spawn 06:20:39, parallel catch-up then sprint

`dipdup_index.level` via public GraphQL (selected raw snapshots):

| UTC | tezos_deposit_ops | tezos_withdrawal_ops | claim_fast | eth_deposit_ev | eth_withdrawal_ev | eth_xtz_dep | token_balance |
|---|--:|--:|--:|--:|--:|--:|--:|
| 06-29 06:20:39 | 7,062,752 | 7,199,834 | 9,151,784 | 5,280,308 | 4,491,473 | 4,188,355 | 0 |
| 06-29 06:51:53 | 7,584,097 | 9,531,039 | 9,344,602 | **rt** 46,722,353 | 18,532,388 | 5,684,473 | 6,197,188 |
| 06-29 08:24:43 | 8,633,643 | 9,876,111 | 9,489,128 | rt | 19,810,783 | 11,314,220 | 6,435,696 |
| 06-29 11:16:03 | 9,325,018 | 10,179,184 | 9,616,112 | rt | 21,203,001 | 18,699,713 | 6,530,044 |
| 06-29 13:54:41 | 9,420,159 | 10,546,320 | 9,746,028 | rt | 21,755,105 | 19,715,712 | 6,694,233 |
| 07-01 09:54:15 | 10,636,480 | **rt** 13,847,068 | 13,699,467 | rt | 43,897,815 | 39,881,213 | 21,919,127 |
| 07-02 12:04:44 | **rt** 13,888,186 | rt | **rt** | syncing 47,037,401 | 47,037,401 | **rt** 47,037,416 | 47,037,401 |

Computed rates, slowest pole `tezos_deposit_operations`:
- first 31 min, before contention bites: **~16.7k lvl/min** (~280 lvl/s)
- contention floor, 46.6-h window 06-29 11:16 → 07-01 09:54: +1,311,462 = **~469 lvl/min** (pairwise 469–503)
- sprint after L1-withdrawal/claim_fast finish + L2 near head, 07-01 09:54 → 07-02 ≤12:04: +3,251,706 in ≤26.2 h = **≥2,071 lvl/min** (edge points only, no interior samples)

`etherlink_token_balance_update`: 0→6.2M in 31 min (empty region), then starved **~17 lvl/s** for ~2.6 h,
6.69M→21.9M over ~44 h, then **21.9M→47.0M in 26.2 h (~16k lvl/min)**.
NB: "6.5M→47M за сутки" in the earlier doc is a conflation — that interval actually took ~73 h.
`etherlink_xtz_deposit_transactions`: bursts ~1,200–1,760 lvl/s, dips to ~68 lvl/s — Subsquid skips empty levels, rate is spiky by design.

Quirk (matters for health checks): indexes already `realtime` keep a **frozen level** (spawn value 13,847,068 / 46,722,353)
until the loop frees up — status=realtime with a stale level during someone else's catch-up is normal.

### Final counters

07-02 12:04: `bridge_operation` **86,497**, `l1_deposit` 31,933, `l1_withdrawal` 53,871, `l2_token_holder` 2,190.
07-02 ~12:52 audit: bridge_operation 86,500 = deposits 31,934 (31,907 FINISHED + 24 inbox-timeout + 3 routing)
+ withdrawals 54,566 (53,799 + 740 CREATED + 27 expired); deposit invariants 0/31,907 violations.

## 1b. Full mainnet reindex from scratch, 2026-08-22 01:51 → 08-23 03:24 UTC

Stack: `-mainnet-staging` (etherlink-bridge-mainnet-staging.dipdup.net), master carrying the matcher
candidate-pool rewrite (PR #43) and the pre-origination outbox-level fix (PR #49). Ranges: L1 5.61M→14.63M,
L2 →51.72M. Probes = public GraphQL every 60 s (`perf/reindex-2026-08/probe.py`, series in `progress.jsonl`);
stage boundaries read from the database's own timestamps, which is what survived the sampler outage below.

**End-to-end: 14 h 47 m** (08-22 12:37:22 redeploy → 08-23 03:24:24 first realtime-side `dipdup_head` rows),
against **3 d 11 h** for the 06-29 run in §1 — **5.6×**.

The wall clock from the wipe is longer and does not belong in that comparison: the schema was created
**01:51:47** on an image predating PR #49, and the inbox backfill sat wedged at 45 rows / level 5,611,063 for
**10 h 44 m** — every owed outbox level below the rollup origination is unresolvable, and the drain died on the
first one ahead of `bulk_create`. The redeploy at 12:37:22 resumed the same cursor (45 rows retained, no wipe);
`pending` fell 6,189 → 3,833 within 27 s as `PendingOutboxLevels.load(floor=…)` dropped the impossible levels,
and `rollup_outbox_message` went 0 → 20 — the first outbox rows ever written on that database.

### Phase 0 — inbox backfill inside on_restart (`dipdup_index` empty)

12:37:22 → 17:52:02 = **5 h 14 m 40 s**, 5,611,063 → ~14.63M ≈ 9.02M levels, **average 478 lvl/s**
(§1: 5 h 18.5 m, 8.14M levels, 425 lvl/s — same phase, ~12 % faster over a span that grew with the chain).

| UTC 08-22 | inbox rows | max(level) | outbox rows | lvl/s |
|---|--:|--:|--:|--:|
| 12:37:22 | 135 | 5,622,542 | 13 | |
| 13:02:43 | 2,623 | 6,378,799 | 565 | 497 |
| 13:28:04 | 4,045 | 7,140,897 | 842 | 501 |
| 13:53:25 | 5,870 | 7,890,560 | 1,215 | 493 |
| 14:18:44 | 7,311 | 8,598,885 | 1,360 | 466 |
| 14:43:59 | 8,325 | 9,242,685 | 1,603 | 425 |
| 15:09:15 | 17,425 | 9,881,641 | 10,840 | 421 |
| 15:34:32 | 22,743 | 10,574,280 | 15,720 | 457 |
| 15:37:34 | 23,246 | 10,658,505 | 16,128 | 463 |

Same shape as §1 — fastest in the first hour, sagging mid-phase — but without §1's 323–405 lvl/s trough.
The outbox counter is not monotone with the inbox one: it advances in drain batches (`_drain_outbox_levels`
commits after its whole loop), which is why 15:09 shows +9,100 inbox and +9,237 outbox in one step.

### Phase 1 — 10 indexes spawn 17:52:02, sync to realtime 08-23 03:24:24

**9 h 32 m.** In §1 the same phase ran 06-29 06:20:39 → 07-02 12:04 ≈ **77.7 h** — **≈8×**, and the whole
end-to-end gain lives here. No interior samples: the sampler was down for this window (see below), so the
per-index catch-up curve that §1 tabulates has no counterpart here. Two changes land in this interval at once —
the matcher candidate-pool rewrite (PR #43, the first backfill measured on it) and whatever the host was
doing — and this run does not separate them.

### Final counters

08-23 16:52 (13.5 h into realtime): `bridge_operation` **93,724**, `l1_deposit` 34,384, `l1_withdrawal` 58,668,
`rollup_inbox_message` 38,157, `rollup_outbox_message` 60,144, `rollup_message_pending_outbox_levels` `[]`.
All 10 indexes realtime; `dipdup_head` for tzkt / etherlink_node / tezos_x_michelson_tzkt fresh within seconds.

### Instrument note — the 13 h hole in `progress.jsonl`

The sampler ran on the workstation as a `systemd --user` unit (`bridge-probe.service`). It survived a Claude Code
process restart with no gap, then stopped at **08-22 15:37:34** and resumed at **08-23 04:37:45** when the machine
came back — `Linger=yes` keeps a unit alive across logout, not across the WSL VM shutting down. The hole swallowed
the end of phase 0, all of phase 1, and the flip to realtime.

The stage boundaries above survived anyway because the indexer records them itself, server-side:
`dipdup_schema.created_at` = wipe, `dipdup_index.created_at` = phase 0 done / indexes spawned,
`dipdup_head.created_at` = realtime. A sampler outage costs the rate curve, not the timeline —
provided the measured system is asked for its own timestamps before the sampler is trusted overnight.
`bridge_operation.created_at` is the on-chain operation time, not the row's insert time, and cannot
substitute for the sampler.

## 2. Realtime hour at lag 0 (chain pace, NOT indexer ceiling)

2026-06-24 22:34:43–23:35:28 UTC (3,645 s), staging instance on the mainnet stack, caught up, lag 0 the whole hour.
Sampler: GraphQL `dipdup_index`/`dipdup_head` every 300 s + `eth_blockNumber` on evm-sonic.baking-bad.org; 13 samples.

| series | Δlevels | rate |
|---|--:|--|
| tezos_head | +600 | **0.165 lvl/s** = 9.9 lvl/min |
| tezos_deposit_operations | +591 | 0.162 lvl/s (steps: plateaus 10–15 min, then +50–150) |
| etherlink_xtz_deposit_transactions | +3,121 | **0.856 lvl/s** = 51.4 lvl/min |
| etherlink_deposit_events | +3,129 | 0.858 lvl/s |

Final lag 0 on both chains (tezos_head == tzkt_head == 13,786,099; eth == evm head == 46,408,229).
The oft-quoted **1.8 lvl/s** is a 91-second burst window same day (15:47:34→15:49:05, 163 levels) — short-window
chain burst, not sustained. These are the **chain's production pace** — a floor observation, says nothing about capacity.

## 3. Self-hosted Subsquid archive ingest (etherlink-archive-mainnet, sqa.eth.ingest)

Full mainnet ingest **block 19 → head 46.34M**: started 2026-06-22 ~23:23 UTC, observed at head 06-24 09:08 UTC;
verifiable average 29.87M blocks in 15 h 53 m = **~522 blocks/s**; sustained log rates **453–511 blocks/s**
(startup samples 459–642); ~26 h extrapolated to reach head, ≤33.75 h observed upper bound.
41× `rpc connection error` + `going offline` pauses over a 2.8-h Loki window — periodic RPC hiccups, self-healed.
At head: `progress: 1 blocks/sec` (mainnet) / `0 blocks/sec` (previewnet, block every ~6 s, rounds down) = chain pace, healthy.

## 4. Busy-block density + unit costs (probe 2026-07-09)

Probe: `etherlink-archive-mainnet.dipdup.net` worker queries (`type: "eth"`, self-hosted sqa; gzip body),
count blocks containing ≥1 ERC-20 Transfer log per 100k-level sample:

| range | busy blocks | share | transfer logs | wall |
|---|--:|--:|--:|--:|
| 46.0–46.1M | 16,208 | **16.2%** | 39,397 | 4.5 s |
| 30.0–30.1M | 45,202 | **45.2%** | 119,200 | 9.4 s |
| 12.0–12.1M | 82,431 | **82.4%** | 218,698 | 16.4 s |

Transfer-busy share **decreases** with chain age (82% → 45% → 16%). Bridge events themselves are ~0.07%
of blocks (31.9k deposits / 44M levels).

Derived unit costs (from §1 rates + these densities, order-of-magnitude):
- L2 empty levels through DipDup: token_balance 0→6.2M in 31 min ≈ **200k lvl/min** → 1k empty blocks ≈ 0.3 s (up to ~2 s when loop busy).
- L2 Transfer-busy blocks: 21.9M→47M at ~16k lvl/min with ~30% mean density → ≈ **11–12 ms per busy block** → 1k busy blocks ≈ 10–15 s.
- L1 levels (TzKT, cost is per-level not per-event): sparse era ~16.7k lvl/min → 1k ≈ 3.6 s; busy era solo ~2,000 lvl/min → 1k ≈ 30 s; busy era contended ~470 lvl/min → 1k ≈ 2.1 min.
- Phase-0 inbox scan: 425 lvl/s → 1k L1 levels ≈ 2.4 s.

## 5. TezosX-shadownet reindexes — transfer validation, n=2 (extracted 2026-07-14)

Same pair (L1 shadownet + TezosX previewnet rollup), same host (`vadim`), two full reindexes two days
apart differing ONLY in the L2 datasource. Ranges: L1 3,044,225 → ~3.82–3.84M head (~0.78M levels),
L2 0 → ~786–808k, Michelson 188,515 →.

**B — 2026-06-19, Subsquid archive wired (PR #38, `etherlink-archive-previewnet.dipdup.net`):**
container start 13:37:58 UTC → ALL indexes realtime by ~14:00–14:01 = **~23 min end-to-end**.
- Phase 0: inbox cursor 23.72M → target 32.82M (≈9.1M messages, cursor starts at first_level's id)
  at ~8.1k msg/s ≈ ~19–20 min. Level-equivalent ~650 lvl/s vs doc formula 425 lvl/s (formula
  conservative ×1.5). NB the unit of work is inbox MESSAGES, not levels: shadownet density
  ≈11.7 msg/lvl; rate invariant is TzKT paging throughput, not lvl/s.
- Разлёт: all 9 indexes spawned and reached head (L2 808,032/808,496 = archive /height; L1 3,840,951)
  within ~1–2 min. Толпа/Спринт did not occur (empty L1 → no pole).
- Formula check: Тишина predicted ~30 min / actual ~20 ✓; «весь L2 в Разлёте» ✓; Толпа+Спринт
  center-formula would predict ~8.6 h — the «пустой L1 быстрее» caveat dominates entirely.

**A — 2026-06-17→18, NO archive (env `ETHERLINK_SUBSQUID_URL` empty; PR #38 config not yet wired):**
L2 backfill crawled the flaky previewnet EVM node (503 storms, `batch_size 100`); the stage model
did NOT apply:
- 216-crash loop 22:02–22:18 (L2Account order-of-arrival bug, fixed in the PR) + restart 00:20:32.
- Tezos/TzKT legs realtime at 00:20:33–42; then frozen for ~10 h (tezos_head stuck at 3,820,388,
  updated_at age 594 min, chain lag grew 637→5,655 lvl) — event loop fully starved by L2 crawl.
- L2 via node: withdrawal_events 83,972 (03:16) → 472,180 (11:52) ≈ 750 lvl/min; xtz_deposit
  ≈1,000 lvl/min; deposit_events + token_balance_update_events NEVER left level 0 in 11.5 h.
  ~2/3 of the range done by last observation; extrapolated full run ≥ a day vs B's 23 min (×60+).
- 00:20→01:26 also a classic 503-wedge window (logs silent 65 min).

Conclusion baked into the doc's §перенос: «Subsquid skips empty blocks» is a property of the
ARCHIVE BEING WIRED, not of the network — the model's stage geometry presumes it. First transfer
check = archive exists for the rollup and its height grows. (Historical note, RESOLVED: on 06-18
the previewnet archive `/height` was stale-REPORTED (250,028) while the worker actually served
blocks to 791,532 — a defect of the upstream sqa router (`_SyncProc.poll()` treated any sync-child
exit incl. code 0 as fatal → crash-loop / stale height). Since then the router was fully rewritten
in-house (`~/archive-router-local`) and deployed on all archives, so `/height` is trustworthy again;
the caveat applies only to readings from before the swap.)

## Sources

Transcripts: `~/.claude/projects/-home-ubuntu-rollup-bridge-indexer/` — reindex: `07928e0f`, `7d567b1c`, `b09ae210`;
realtime hour: `0bdc8258`; ingest: `08df2e30` + `-home-ubuntu-archive-router-local/` `619ee70f`, `398336d7`;
tezosx-shadownet reindexes A/B: `4764427f` (06-17..18, node crawl), `efc1f8be` (06-19, archive),
`08df2e30` (archive /height forensics). Extraction 2026-07-14 by subagents; raw JSON series in those transcripts.
Live GraphQL recipe re-verified 2026-07-09 (both mainnet endpoints realtime, glued to head).
