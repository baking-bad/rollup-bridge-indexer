# mainnet-staging backfill perf — ground truth

Investigation 2026-06-05. Endpoint `etherlink-bridge-mainnet-staging.dipdup.net`.
All facts below are **measured/observed**, not inferred. Opinions/open questions at the bottom.

## 1. Throughput measurements (per-index `dipdup_index.level`, levels/min)

Old node (`sharm`), window T0→T2 = 64.9 min · New node, two windows (66.7 / 77.2 min).

| Index | old (T0→T2) | new #1 | new #2 |
|---|--:|--:|--:|
| etherlink_token_balance_update | 1,335 | 4,455 | 7,706 |
| etherlink_withdrawal_events | 552 | 3,543 | 6,593 |
| etherlink_xtz_deposit_transactions | 2,212 | 2,681 | 2,290 |
| tezos_withdrawal_operations | 321 | 2,312 | 2,625 |
| tezos_claim_fast_withdrawal | 100 | 1,199 | 998 |
| tezos_deposit_operations | **52** | 425 | 326 |
| **AGGREGATE** | **4,571** | **14,617** | **20,537** |
| **vs old** | ×1 | **×3.2** | **×4.5** |

Timestamps (UTC): T0 10:52:29 · T1 11:02:41 · T2 11:57:24 (old) · T_new0 19:14:21 · T_new1 20:21:01 · T_new2 21:38:13 (new).
Note: 17:32→19:14 window spans the node migration (downtime) — not used for rates.

Key reading: aggregate gain is **×3.2–4.5**. Per-index ×7–12 figures are **contention redistribution**, not real per-index speedup — on `sharm`, `xtz_deposit` alone consumed **48%** of total throughput (2,212 of 4,571) while others starved; on the new node work spreads out. `tezos_deposit_operations` stays the slowest pole (~375/min) even after the move.

## 2. Datasource is NOT the bottleneck (raw archive replication)

Replicated DipDup's exact `iter_transactions`/`iter_events` archive protocol (GET `{base}/{level}/worker` → POST query with `from_`/topic filters; api-key headers; `User-Agent` required else Cloudflare 1010) on the lagging range:

| Probe | result |
|---|---|
| transactions (from 0x0 / 0x…feed) | 1,399,522 levels in 15.7s = **5,331,910 lvl/min** |
| events (12 ERC-20 Transfer) | 701,618 levels in 10.3s = **4,106,489 lvl/min** |
| per query | ~1.5–2.0s, advances 100–230k levels, sparse matches (~200–600 blocks) |
| archive head | 44,705,547 (ahead of indexer) |
| EVM node `eth_blockNumber` | 44,706,061, ~0.9s response |

→ Archive delivers ~4–5.3M lvl/min; indexer ingests ~4.6–20k lvl/min ⇒ **indexer uses ~0.1–0.5% of datasource capacity.** The bottleneck is in-process.

## 3. Code-path facts (dipdup `feat/8.6.0-sqd`, src read)

- **evm.transactions does NOT hit the node during backfill.** `indexes/_subsquid.py::_synchronize` picks the node only when `subsquid_head − index_level ≤ NODE_LAST_MILE (=128)`. Lag was ~22M ⇒ pure Subsquid path. Only 1 cheap `node.get_head_level()` per sync cycle.
- **`on_xtz_deposit` handler makes no node calls** — validates fields on the tx itself + reads xtz `EtherlinkToken`/`TezosTicket` from DB.
- **`transaction` watchdog = "no DB write for N s"**, NOT an evm.transactions stall. Heartbeat is in `TransactionManager.in_transaction` (`transactions.py:48`). Default timeout 10s, action=warning. (Two unrelated `Watchdog` classes: `utils.Watchdog` for realtime ws vs `watchdog.WatchdogManager` configurable — our WARN is the latter.)
- **`yield_by_level` yields only non-empty levels** (`fetcher.py`) ⇒ ~one DB commit per matched block (~400/query), not per empty level. Rules out "commit per empty level".
- **on_transfer handler load is trivial** — `l2_token_holder` had only 219 rows / 34,124 total tx_count at level 18.2M. Not the bottleneck.
- **No dipdup regression.** Branch is +6 commits over PyPI 8.5.2; the v2.archive code path is byte-identical. The only perf-relevant change (503→`ratelimit_sleep` in `http.py`) is **uncommitted** in the local checkout ⇒ not in the deployed image (image installs pushed branch HEAD).
- Logs over 1h: zero ERROR/Exception/ratelimit/429/websocket-reconnect. Only WARN = the `transaction` watchdog + `Incorrect XTZ Deposit … ignored` (normal handler validation).

## 4. Host metrics (Prometheus/node-exporter, current snapshot, all Swarm nodes)

- `steal ≈ 0` everywhere → not hypervisor CPU-steal.
- `iowait ≈ 0–2%` everywhere → no node disk-bandwidth-saturated.
- No `container_spec_cpu_quota` / cfs_throttled → containers not cgroup-CPU-capped.
- cAdvisor here lacks swarm-service labels; node-exporter `nodename` = container-id ⇒ **per-container CPU not attributable from this Grafana** (need node-side `docker stats`/Portainer). pg-exporter (`pg_*`) present.

## 5. Behavioral signature

Indexes advance in a **round-robin / bursty** pattern with large per-index rate variance (e.g. `xtz_deposit` 578→2,517/min across adjacent old-node windows). Classic signature of **one serial resource** — DipDup = single asyncio event loop (≤1 core) + single DB connection. Liveness = `dipdup_index.updated_at` freshness (status stays `realtime`/`syncing` even if frozen).

## 6. Open questions / opinions (NOT ground truth)

- **×3.2–4.5 is not raw single-core clock** (clock ≈ ×1.5–2 between machines). Unconfirmed candidates: (a) host CPU contention on busy `sharm` (CFS run-queue, invisible as steal/iowait); (b) **DB commit/fsync latency** (per-op, invisible in iowait%) blocking the serial loop; (c) memory-pressure/swap; (d) **restart confound** — new node = also a fresh process.
- **DB-resource vs indexer-resource not separated.** Decisive test: `docker stats` indexer container under sync — ~100% of a core = compute/matcher-bound; low CPU + slow = DB-latency-bound. Cross-check `pg_stat_database.blk_write_time` + commit rate. Rule out restart confound by restarting the process WITHOUT moving nodes.
- Leading optimization target (team-flagged): `bridge_matcher` per-batch cost over growing tables + per-event Python + per-level commits — i.e. the event loop, not the datasource.
