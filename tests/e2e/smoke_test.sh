#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/tests/e2e/smoke.env"
PKG="rollup_bridge_indexer"

OVERLAYS="${OVERLAYS:-mainnet shadownet tezosx-shadownet}"
BOOT_NETWORK="${BOOT_NETWORK:-shadownet}"

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

if [[ -z "$IMAGE" ]]; then
    IMAGE="rollup-bridge-indexer:smoke"
    step "Building image ${IMAGE}"
    docker build -t "$IMAGE" "$REPO_ROOT"
else
    step "Using prebuilt image ${IMAGE}"
fi

dd() {
    local network="$1"; shift
    docker run --rm --env-file "$ENV_FILE" -e NETWORK="$network" --entrypoint dipdup "$IMAGE" "$@"
}

step "Package name resolves to '${PKG}'"
NAME="$(docker run --rm --entrypoint python "$IMAGE" -c \
    "from dipdup.env import get_package_path, reload_env; reload_env(); \
from dipdup.package import DipDupPackage; print(DipDupPackage(get_package_path('${PKG}')).name)")"
echo "resolved package name: ${NAME}"
[[ "$NAME" == "$PKG" ]] || fail "package resolved as '${NAME}', expected '${PKG}' (DIPDUP_PACKAGE_PATH basename is wrong)"
green "OK: package name is ${PKG}"

step "dipdup package verify (${BASE_CFG} + ${BOOT_NETWORK})"
dd "$BOOT_NETWORK" -c "$BASE_CFG" -c "$(overlay_cfg "$BOOT_NETWORK")" package verify
green "OK: package verify passed (initialize + handlers/hooks/types import)"

for net in $OVERLAYS; do
    step "dipdup config export --unsafe (${BASE_CFG} + ${net})"
    dd "$net" -c "$BASE_CFG" -c "$(overlay_cfg "$net")" config export --unsafe >/dev/null
    green "OK: ${net} config valid"
done

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

    docker run -d --name "$IDX" --network "$NET" \
        --env-file "$ENV_FILE" -e NETWORK="$BOOT_NETWORK" -e POSTGRES_HOST="$DB" \
        "$IMAGE" -c "$BASE_CFG" -c "$(overlay_cfg "$BOOT_NETWORK")" run >/dev/null

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
