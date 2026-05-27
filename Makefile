.ONESHELL:
.DEFAULT_GOAL: all

py := uv run

# The package lives at the repo root (rollup_bridge_indexer is a self-symlink -> .),
# so lint targets and the dipdup config are all relative to the root.
source_dir := .
unit_tests_dir := tests/unit

# dipdup.yaml is at the repo root, so a bare -c . locates it and the package.
dipdup_args := -c .

test:
	PYTHONPATH=. $(py) pytest $(unit_tests_dir)

install:
	uv sync --all-extras --all-groups --link-mode symlink --locked

black:
	$(py) black $(source_dir)

ruff:
	$(py) ruff check --fix-only --show-fixes $(source_dir)

mypy:
	$(py) mypy $(source_dir)

lint: black ruff mypy

check-config:
	@test -n "$(NET)" -a -n "$(ENV)" || (echo "usage: make check-config NET=<network> ENV=<path-to-env-file>"; exit 1)
	set -a; . $(ENV); set +a
	$(py) dipdup -c . -c configs/$(NET).yaml config export --unsafe > /dev/null
	@echo "Config OK: $(NET)"

wipe:
	$(py) dipdup $(dipdup_args) schema wipe --force

init:
	$(py) dipdup $(dipdup_args) init

run:
	$(py) dipdup $(dipdup_args) run

up:
	docker compose up --build --remove-orphans --force-recreate --abort-on-container-exit

down:
	docker compose down --volumes

# Build the image and verify the indexer can actually start inside it (catches the
# flatten/packaging regression where DipDup resolved the package as `app`). Pass
# BOOT=1 to also boot the indexer against a throwaway Postgres (needs TZKT access).
docker-test:
	tests/e2e/smoke_test.sh $(if $(filter 1,$(BOOT)),--boot)

# Full production-readiness sequence (the e2e gate): lint in check-mode + unit tests +
# docker smoke (build, package verify, config validation, image audit). Run before
# publishing/redeploying. BOOT=1 also boots the indexer against throwaway Postgres.
prod-check:
	tests/e2e/run_all.sh $(if $(filter 1,$(BOOT)),--boot)

update:         ## Update dependencies
	dipdup self update -q
	uv sync --all-extras --all-groups --link-mode symlink -U

# --- Stripped, block-bounded test indexer for tezosx-shadownet ---
TEST_ENV ?= /home/ubuntu/deployments/stacks/etherlink-bridge-indexer/.env.tezosx-shadownet
TEST_OVERRIDE ?= tests/stand/tezosx-shadownet-test.env
TEST_CONFIG := tests/stand/tezosx-shadownet-test.yaml

check-test-config:
	set -a; . $(TEST_ENV); . $(TEST_OVERRIDE); set +a
	$(py) dipdup -c $(TEST_CONFIG) config export --unsafe > /dev/null
	@echo "Test config OK"

test-indexer:
	@test -f $(TEST_OVERRIDE) || (echo "missing $(TEST_OVERRIDE) — cp tests/stand/tezosx-shadownet-test.env.default $(TEST_OVERRIDE) and set the block window"; exit 1)
	set -a; . $(TEST_ENV); . $(TEST_OVERRIDE); set +a
	rm -f "$${SQLITE_PATH:-/tmp/bridge_tezosx_test.sqlite}"
	$(py) dipdup -c $(TEST_CONFIG) run

inspect-test:
	set -a; . $(TEST_OVERRIDE); set +a
	$(py) python tests/stand/verify_test_indexer.py "$${SQLITE_PATH:-/tmp/bridge_tezosx_test.sqlite}"
