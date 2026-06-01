#!/usr/bin/env bash
#
# Production-readiness gate — the full sequence that says "this repo state is shippable".
# Runs every script-based check in order, stops at the first failure. Run before
# publishing/redeploying (`make prod-check`, or `make prod-check BOOT=1` for the full boot).
#
# What it runs (all NON-mutating — it asserts, it does not fix):
#   1. black --check    formatting is clean         (`make black` fixes)
#   2. ruff check       lint is clean               (`make ruff` fixes)
#   3. mypy             types check
#   4. pytest unit      unit suite passes
#   5. smoke_test.sh    image builds, package resolves + verifies, every deployed overlay's
#                       config validates, no test code/caches shipped, (+ boot with --boot)
#
# NOT included (by design): tests/stand/ — a manual, secrets-bound bug-repro harness, not a
# pass/fail readiness gate. See tests/stand/TESTING.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
step()  { printf '\n########## %s ##########\n' "$*"; }
fail()  { red "FAIL: $*"; exit 1; }

py="uv run"

step "1/5 black --check (formatting)"
$py black --check --diff . || fail "black: code is not formatted (run \`make black\`)"
green "OK: formatting clean"

step "2/5 ruff check (lint)"
$py ruff check . || fail "ruff: lint errors (run \`make ruff\`)"
green "OK: lint clean"

step "3/5 mypy (types)"
$py mypy . || fail "mypy: type errors"
green "OK: types clean"

step "4/5 pytest unit"
PYTHONPATH=. $py pytest tests/unit || fail "unit tests failed"
green "OK: unit suite passed"

step "5/5 docker smoke (build + package verify + config + image audit)"
# Pass through --boot etc. No image tag → smoke_test.sh builds a fresh image.
tests/e2e/smoke_test.sh "$@" || fail "docker smoke failed"

step "PRODUCTION-READINESS: PASS"
green "All gates passed — repo state is shippable."
