#!/usr/bin/env bash
# Four-arm protocol for the outbox-fetch-failure case. See README.md.
#
#   CONTROL         proxy passes everything through       -> both outbox messages indexed
#   TREATMENT       proxy fails one outbox level          -> indexer aborts, nothing saved
#   TREATMENT-AGAIN restart, SAME db, fault STILL armed   -> prod stayed broken across restarts
#   RECOVERY        proxy healthy again, SAME db, no wipe -> does the restart get them back?
#
# The script ASSERTS: every arm's facts go to $OUT/arms.json, an arm that cannot mean what it
# is supposed to mean aborts the run, and the last thing it does is run the verifier and exit
# with its status.
#
#   exit 0  GREEN — the outbox messages survived the failure
#   exit 1  RED   — they are gone (the current, unfixed behaviour)
#   exit 2  VOID  — the run proves nothing (harness/proxy/arm-precondition failure)
#
# Usage: tests/stand/cases/outbox_fetch_failure/run_arms.sh
#
# Env: SQLITE_PATH / PROXY_PORT / PROXY_CACHE_DIR / OUT_DIR override window.env, so a second
# sequence can run beside this one from another worktree (the response cache is only read
# after the prewarm and may be shared). ARM_TIMEOUT bounds a single dipdup arm.
#
# HARNESS_SELFTEST proves the gate can still move in both directions — see README.md:
#   green  rewind the inbox cursor before each restart arm (what a real fix must achieve)
#          => the sequence must end GREEN, exit 0
#   void   run TREATMENT against a dead TzKT so it dies before the inbox bulk_create
#          => the sequence must abort VOID, exit 2 (never GREEN off an empty database)
set -u

cd "$(dirname "$0")/../../../.." || exit 1
CASE=outbox_fetch_failure
CASE_DIR=tests/stand/cases/$CASE

# Shell env wins over window.env; window.env holds the defaults and every window constant.
_env_sqlite=${SQLITE_PATH:-}
_env_port=${PROXY_PORT:-}
_env_cache=${PROXY_CACHE_DIR:-}
set -a
# shellcheck source=window.env
. "$CASE_DIR/window.env"
set +a
SQLITE_PATH=${_env_sqlite:-$SQLITE_PATH}
PROXY_PORT=${_env_port:-$CASE_PROXY_PORT}
PROXY_CACHE_DIR=${_env_cache:-$CASE_PROXY_CACHE_DIR}
FAIL_STATUS=$CASE_FAIL_STATUS
FAIL_LEVEL=$CASE_FAIL_LEVEL
OUT=${OUT_DIR:-/tmp/outbox_fetch_failure}
ARM_TIMEOUT=${ARM_TIMEOUT:-600}
SELFTEST=${HARNESS_SELFTEST:-}
ARGS="-e tests/stand/tezosx.env -e ${CASE_DIR}/window.env -c ${CASE_DIR}/config.yaml"
VERIFY="python3 -m tests.stand.cases.${CASE}.verify"

# A fix must re-reach the external message whose outbox level was lost; the self-test does it
# by hand, rewinding the cursor to the sentinel so the restart replays the page.
REWIND_SQL='DELETE FROM rollup_inbox_message WHERE id > ?'
SEED_SENTINEL_SQL=$(
	cat <<-'SQL'
		INSERT OR REPLACE INTO rollup_inbox_message
		    (id, level, "index", type, message, parameters_hash, created_at, updated_at)
		VALUES (?, 0, 0, 'external', '{}', NULL, datetime('now'), datetime('now'))
	SQL
)

export PROXY_PORT PROXY_CACHE_DIR SQLITE_PATH PYTHONPATH=.
mkdir -p "$OUT"
: >"$OUT/arms.jsonl"

write_arms_json() { # $1 = verdict state, $2 = note
	python3 - "$OUT/arms.jsonl" "$OUT/arms.json" "$1" "$2" <<-'PY'
		import json
		import os
		import sys

		jsonl, out, state, note = sys.argv[1:5]
		arms = [json.loads(line) for line in open(jsonl) if line.strip()]
		window = {k: v for k, v in os.environ.items() if k.startswith(('CASE_', 'ROLLUP_SYNC_'))}
		report = {'case': 'outbox_fetch_failure', 'window': window, 'arms': arms, 'verdict': {'state': state, 'note': note}}
		with open(out, 'w') as fh:
		    json.dump(report, fh, indent=2)
		    fh.write('\n')
		print(f'{out}: {state} ({note})')
	PY
}

port_pid() { ss -lptn "sport = :$PROXY_PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }

proxy_stop() { # returns 1 if the port is still held — never pretend a stale proxy is gone
	local pid
	for _ in $(seq 1 20); do
		pid=$(port_pid)
		[ -z "$pid" ] && return 0
		kill "$pid" 2>/dev/null
		sleep 0.5
	done
	pid=$(port_pid)
	[ -n "$pid" ] && kill -9 "$pid" 2>/dev/null
	sleep 1
	[ -z "$(port_pid)" ]
}

die() { # a run that cannot mean what it is supposed to mean is VOID, not RED
	echo
	echo "VOID: $*" >&2
	write_arms_json VOID "$*"
	proxy_stop
	exit 2
}

trap 'proxy_stop >/dev/null 2>&1' EXIT

proxy_start() { # $1 = arm, $2 = PROXY_FAIL_LEVELS value
	proxy_stop || die "port $PROXY_PORT is still held by another process"
	PROXY_TOKEN="$1-$$-$(date +%s%N)"
	PROXY_FAIL_LEVELS="$2" PROXY_FAIL_STATUS="$FAIL_STATUS" PROXY_TOKEN="$PROXY_TOKEN" PROXY_LOG="$OUT/$1.proxy.log" \
		setsid uv run python "$CASE_DIR/proxy.py" >"$OUT/$1.proxy.out" 2>&1 &
	# Identity, not liveness: the arm must talk to the proxy IT started, with ITS fault spec.
	uv run python "$CASE_DIR/proxy.py" check "$PROXY_PORT" "$PROXY_TOKEN" "$2" "$FAIL_STATUS" || {
		cat "$OUT/$1.proxy.out"
		die "arm $1 is not talking to the proxy it started (see $OUT/$1.proxy.out)"
	}
}

sql() { # $1 = statement with ? placeholders, $2.. = integer params (sqlite3(1) is not installed)
	python3 - "$SQLITE_PATH" "$@" <<-'PY'
		import sqlite3
		import sys

		conn = sqlite3.connect(sys.argv[1])
		cur = conn.execute(sys.argv[2], tuple(int(a) for a in sys.argv[3:]))
		conn.commit()
		print(f'  sql: {cur.rowcount} row(s)')
	PY
}

fact() { printf '%s' "$1" | python3 -c 'import json,sys;print(json.load(sys.stdin)[sys.argv[1]])' "$2"; }

run_arm() { # $1 = arm, $2 = PROXY_FAIL_LEVELS value, $3.. = extra env lines for this arm only
	local arm=$1 spec=$2 rc
	shift 2
	{
		echo "SQLITE_PATH=$SQLITE_PATH"
		echo "ROLLUP_NODE_URL=http://127.0.0.1:$PROXY_PORT/"
		for line in "$@"; do echo "$line"; done
	} >"$OUT/$arm.env"

	echo
	echo "=== $arm (fail=${spec:-none}/$FAIL_STATUS) ==="
	proxy_start "$arm" "$spec"
	timeout --kill-after=30 "$ARM_TIMEOUT" uv run dipdup $ARGS -e "$OUT/$arm.env" run >"$OUT/$arm.log" 2>&1
	rc=$?
	if [ "$rc" = 124 ]; then
		echo "arm $arm hit ARM_TIMEOUT=${ARM_TIMEOUT}s and was killed"
	fi
	ARM_FACTS=$($VERIFY --facts "$arm" "$rc" "$spec" "$FAIL_STATUS" "$SQLITE_PATH") ||
		die "could not collect facts for arm $arm"
	printf '%s\n' "$ARM_FACTS" >>"$OUT/arms.jsonl"
	echo "$ARM_FACTS"
}

# --- Prewarm: every level the drain can ask for must already be cached. A cold entry is
# fetched under the datasource's request_timeout (4s) through the proxy's 30s upstream path,
# and the resulting TimeoutError is indistinguishable from the injected fault. ---
uv run python "$CASE_DIR/proxy.py" prewarm "$CASE_PREWARM_FIRST_LEVEL" "$CASE_PREWARM_LAST_LEVEL" "$CASE_PREWARM_ENTRIES" ||
	die "prewarm did not leave $CASE_PREWARM_ENTRIES cached entries in $PROXY_CACHE_DIR"

# --- CONTROL: healthy node, fresh db. Anything but a complete index means the window rotted. ---
rm -f "$SQLITE_PATH"
run_arm control ''
[ "$(fact "$ARM_FACTS" exit_code)" = 0 ] || die "CONTROL must exit 0 (got $(fact "$ARM_FACTS" exit_code)); see $OUT/control.log"
[ "$(fact "$ARM_FACTS" outbox_levels_missing)" = 0 ] || die "CONTROL did not index $CASE_OUTBOX_LEVELS — the window no longer holds them"
[ "$(fact "$ARM_FACTS" outbox_rows)" = "$CASE_OUTBOX_ROWS" ] || die "CONTROL left $(fact "$ARM_FACTS" outbox_rows) outbox rows, expected $CASE_OUTBOX_ROWS"

# --- TREATMENT: the fault. It must crash IN the outbox drain: after the inbox bulk_create
# (rows committed) and before any outbox row is flushed. A crash before the bulk_create leaves
# an empty db, RECOVERY degenerates into a clean CONTROL run and the case would go GREEN with
# no fix at all — that state is VOID, not RED. ---
rm -f "$SQLITE_PATH"
if [ "$SELFTEST" = void ]; then
	run_arm treatment "$FAIL_LEVEL" 'TZKT_URL=http://127.0.0.1:1'
else
	run_arm treatment "$FAIL_LEVEL"
fi
[ "$(fact "$ARM_FACTS" exit_code)" != 0 ] || die "TREATMENT exited 0 — the injected $FAIL_STATUS never reached the caller"
[ "$(fact "$ARM_FACTS" transfers_present)" = 2 ] ||
	die "TREATMENT crashed before the inbox bulk_create ($(fact "$ARM_FACTS" transfers_present)/2 transfers committed) — nothing is stranded, the run proves nothing"
[ "$(fact "$ARM_FACTS" outbox_rows)" = 0 ] ||
	die "TREATMENT left $(fact "$ARM_FACTS" outbox_rows) outbox rows — the drain was not interrupted where the case needs it"
grep -qE 'rollup_message\.py|ClientResponseError' "$OUT/treatment.log" ||
	die "TREATMENT's traceback does not mention rollup_message.py — it died somewhere else"
TREATMENT_MAX_ID=$(fact "$ARM_FACTS" max_inbox_id)

# Prod always carries the previous page's level=0 cursor sentinel; this window is one page, so
# TREATMENT leaves none. Seed it so both restart arms inherit a prod-shaped database.
sql "$SEED_SENTINEL_SQL" "$CASE_SENTINEL_SEED_ID" || die "could not seed the level=0 cursor sentinel"
echo "seeded level=0 cursor sentinel at id $CASE_SENTINEL_SEED_ID"

# --- TREATMENT-AGAIN: the node is still broken, as it was in production across many restarts.
# A fix that remembers the pending level over one restart but forgets it on the next must not
# reach RECOVERY with its state intact. Either exit code is legitimate here (a fix retries and
# crashes again; the unfixed code never asks again), so only the database is asserted. ---
if [ "$SELFTEST" = green ]; then
	sql "$REWIND_SQL" "$CASE_SENTINEL_SEED_ID"
fi
run_arm treatment_again "$FAIL_LEVEL"
[ "$(fact "$ARM_FACTS" transfers_present)" = 2 ] || die "TREATMENT-AGAIN lost the committed inbox transfers — the db was wiped between arms"

# --- RECOVERY: healthy node, same db, no wipe. The verdict is taken on what it leaves. ---
if [ "$SELFTEST" = green ]; then
	sql "$REWIND_SQL" "$CASE_SENTINEL_SEED_ID"
fi
run_arm recovery ''
[ "$(fact "$ARM_FACTS" max_inbox_id)" -gt "$TREATMENT_MAX_ID" ] ||
	die "RECOVERY did not advance the inbox cursor past TREATMENT's ($TREATMENT_MAX_ID) — the restart did not run the backfill"

proxy_stop || die "port $PROXY_PORT is still held after the last arm"

echo
echo "=== verdict (state left by RECOVERY) ==="
$VERIFY "$SQLITE_PATH"
rc=$?
write_arms_json "$([ "$rc" = 0 ] && echo GREEN || echo RED)" "verify.py exit $rc"
echo "logs, per-arm env files and arms.json in $OUT"
exit $rc
