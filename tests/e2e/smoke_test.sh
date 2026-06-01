#!/usr/bin/env bash
#
# Docker smoke test: prove the image builds and the indexer can actually START, using the
# *exact* invocation the production stacks use.
#
# This guards the canonical-flattened-layout packaging (the repo root *is* the
# `rollup_bridge_indexer` package, reached via the committed `rollup_bridge_indexer -> .`
# self-symlink). The flatten regression built a perfectly fine image whose indexer could
# not boot: DipDup derives the package name from the basename of DIPDUP_PACKAGE_PATH, so
# pointing it at /opt/app named the package `app` and `package.initialize()` (which runs on
# every command) failed with a PermissionError on the root-owned package dir.
#
# The command form below MIRRORS the deployment compose
# (/home/ubuntu/deployments/stacks/etherlink-bridge-indexer/docker-compose.yml), which runs:
#     dipdup -c . -c configs/${NETWORK}.yaml ${DIPDUP_COMMAND:-run}
# from WORKDIR /opt/app (the canonical flattened form, same as `make run`) — so the test
# exercises exactly what is actually deployed.
#
# Usage:
#   tests/e2e/smoke_test.sh [IMAGE_TAG] [--boot]
#
#   IMAGE_TAG   Test this existing image instead of building one (CI passes the tag it
#               just built). When omitted, the script builds `rollup-bridge-indexer:smoke`.
#   --boot      Also boot the indexer against a throwaway Postgres and assert it gets
#               through DB setup + schema init + the on_reindex hook (the project's own
#               code) without a structural error. Needs network access to TZKT_URL.
#
# Env overrides: OVERLAYS="mainnet shadownet ..." picks which network overlays to validate
# (default = the live deployment targets). BOOT_NETWORK picks the overlay to boot.
#
# Exit code is non-zero on the first failed check.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/tests/e2e/smoke.env"
PKG="rollup_bridge_indexer"

# Live deployment targets (mainnet-staging reuses the mainnet overlay via NETWORK=mainnet).
OVERLAYS="${OVERLAYS:-mainnet shadownet tezosx-shadownet}"
BOOT_NETWORK="${BOOT_NETWORK:-shadownet}"

# WORKDIR-relative paths — identical to the deployment compose `command:`. The image
# WORKDIR is /opt/app, so `-c .` loads the base config + resolves the package.
BASE_CFG="."
overlay_cfg() { echo "configs/$1.yaml"; }

IMAGE=""
BOOT=0
for arg in "$@"; do
    case "$arg" in
        --boot) BOOT=1 ;;
        -*) echo "unknown flag: $arg" >&2; exit 2 ;;
        *) IMAGE="$arg" ;;
    esac
done

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
step()  { printf '\n=== %s ===\n' "$*"; }
fail()  { red "FAIL: $*"; exit 1; }

# --- Build (unless an image tag was supplied) ---------------------------------
if [[ -z "$IMAGE" ]]; then
    IMAGE="rollup-bridge-indexer:smoke"
    step "Building image ${IMAGE}"
    docker build -t "$IMAGE" "$REPO_ROOT"
else
    step "Using prebuilt image ${IMAGE}"
fi

# Run dipdup in the image exactly as the deployment compose would (same WORKDIR, same
# env-file contract), with NETWORK overridable per overlay.
dd() {
    local network="$1"; shift
    docker run --rm --env-file "$ENV_FILE" -e NETWORK="$network" --entrypoint dipdup "$IMAGE" "$@"
}

# --- 1. Package name resolves to rollup_bridge_indexer (not the WORKDIR basename) ---
step "Package name resolves to '${PKG}'"
NAME="$(docker run --rm --entrypoint python "$IMAGE" -c \
    "from dipdup.env import get_package_path, reload_env; reload_env(); \
from dipdup.package import DipDupPackage; print(DipDupPackage(get_package_path('${PKG}')).name)")"
echo "resolved package name: ${NAME}"
[[ "$NAME" == "$PKG" ]] || fail "package resolved as '${NAME}', expected '${PKG}' (DIPDUP_PACKAGE_PATH basename is wrong)"
green "OK: package name is ${PKG}"

# --- 2. package verify: runs package.initialize() as the runtime user + imports all submodules ---
step "dipdup package verify (${BASE_CFG} + ${BOOT_NETWORK})"
dd "$BOOT_NETWORK" -c "$BASE_CFG" -c "$(overlay_cfg "$BOOT_NETWORK")" package verify
green "OK: package verify passed (initialize + handlers/hooks/types import)"

# --- 3. Every deployed network's config validates (production command form) ---
for net in $OVERLAYS; do
    step "dipdup config export --unsafe (${BASE_CFG} + ${net})"
    dd "$net" -c "$BASE_CFG" -c "$(overlay_cfg "$net")" config export --unsafe >/dev/null
    green "OK: ${net} config valid"
done

# --- 4. Image hygiene: no test code or tool caches shipped in the prod image ---
# `.dockerignore` excludes tests/** and **/.*_cache/** — this asserts it actually held
# (a cache's *.json once leaked past the `!**/*.json` re-include). Scoped to /opt/app and
# excluding the venv's site-packages (deps bundle their own tests/, e.g. psutil/tests).
step "Image hygiene: no tests/ or caches under /opt/app"
LEAKED="$(docker run --rm --entrypoint sh "$IMAGE" -c \
    "find /opt/app -not -path '*/site-packages/*' \
        \( -path '*/tests/*' -o -name tests -o -name '.mypy_cache' -o -name '.ruff_cache' \
           -o -name '.pytest_cache' -o -name '__pycache__' \) 2>/dev/null")"
if [[ -n "$LEAKED" ]]; then
    echo "$LEAKED" | head -20
    fail "test code / tool caches leaked into the image (see .dockerignore)"
fi
green "OK: image carries no test code or tool caches"

# --- 5. Optional: boot the indexer against a throwaway Postgres ---------------
if [[ "$BOOT" == "1" ]]; then
    step "Booting indexer against throwaway Postgres (overlay: ${BOOT_NETWORK})"
    NET="bridge_smoke_net_$$"
    DB="bridge_smoke_db_$$"
    IDX="bridge_smoke_idx_$$"
    cleanup() { docker rm -f "$IDX" "$DB" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
    trap cleanup EXIT

    docker network create "$NET" >/dev/null
    docker run -d --name "$DB" --network "$NET" \
        -e POSTGRES_USER=dipdup -e POSTGRES_DB=dipdup -e POSTGRES_PASSWORD=changeme \
        postgres:17 >/dev/null
    for _ in $(seq 1 30); do
        docker exec "$DB" pg_isready -U dipdup >/dev/null 2>&1 && break
        sleep 1
    done

    # Same command shape as the deployment compose: absolute config paths + `run`.
    docker run -d --name "$IDX" --network "$NET" \
        --env-file "$ENV_FILE" -e NETWORK="$BOOT_NETWORK" -e POSTGRES_HOST="$DB" \
        "$IMAGE" -c "$BASE_CFG" -c "$(overlay_cfg "$BOOT_NETWORK")" run >/dev/null

    # The indexer reaches the sync loop and then retries the dummy chain RPCs forever;
    # success is "Initializing database schema" with no Traceback before the retries start.
    deadline=$(( $(date +%s) + 90 ))
    ok=0
    while [[ $(date +%s) -lt $deadline ]]; do
        logs="$(docker logs "$IDX" 2>&1 || true)"
        if grep -q "Traceback" <<<"$logs"; then
            echo "$logs" | tail -40
            fail "indexer crashed during boot"
        fi
        if grep -q "Initializing database schema" <<<"$logs" \
           && grep -qE "HTTP request attempt|Synchronizing|realtime|on_restart" <<<"$logs"; then
            ok=1; break
        fi
        # Container exiting early (before the sync loop) is a failure.
        if [[ "$(docker inspect -f '{{.State.Running}}' "$IDX")" != "true" ]]; then
            echo "$logs" | tail -40
            fail "indexer container exited before reaching the sync loop"
        fi
        sleep 2
    done
    [[ "$ok" == "1" ]] || { docker logs "$IDX" 2>&1 | tail -40; fail "indexer did not reach the sync loop within 90s"; }
    green "OK: indexer booted through DB setup + schema init + on_reindex into the sync loop"
fi

step "All Docker smoke checks passed"
green "PASS"
