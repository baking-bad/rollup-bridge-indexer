#!/usr/bin/env bash

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
tests/e2e/smoke_test.sh "$@" || fail "docker smoke failed"

step "PRODUCTION-READINESS: PASS"
green "All gates passed — repo state is shippable."
