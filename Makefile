.ONESHELL:
.DEFAULT_GOAL: all

py := uv run

# The package lives at the repo root (rollup_bridge_indexer is a self-symlink -> .),
# so lint targets and the dipdup config are all relative to the root.
source_dir := .
unit_tests_dir := tests

# dipdup.yaml is at the repo root, so a bare -c . locates it and the package.
dipdup_args := -c .

test:
	PYTHONPATH=. $(py) pytest tests/

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

update:         ## Update dependencies
	dipdup self update -q
	uv sync --all-extras --all-groups --link-mode symlink -U

# --- Stripped, block-bounded test indexer for tezosx-shadownet ---
TEST_ENV ?= /home/ubuntu/deployments/stacks/etherlink-bridge-indexer/.env.tezosx-shadownet
TEST_OVERRIDE ?= ./tezosx-shadownet-test.env
TEST_CONFIG := configs/tezosx-shadownet-test.yaml

check-test-config:
	set -a; . $(TEST_ENV); . $(TEST_OVERRIDE); set +a
	$(py) dipdup -c $(TEST_CONFIG) config export --unsafe > /dev/null
	@echo "Test config OK"

test-indexer:
	@test -f $(TEST_OVERRIDE) || (echo "missing $(TEST_OVERRIDE) — cp tezosx-shadownet-test.env.default $(TEST_OVERRIDE) and set the block window"; exit 1)
	set -a; . $(TEST_ENV); . $(TEST_OVERRIDE); set +a
	rm -f "$${SQLITE_PATH:-/tmp/bridge_tezosx_test.sqlite}"
	$(py) dipdup -c $(TEST_CONFIG) run

inspect-test:
	set -a; . $(TEST_OVERRIDE); set +a
	$(py) python verify_test_indexer.py "$${SQLITE_PATH:-/tmp/bridge_tezosx_test.sqlite}"
