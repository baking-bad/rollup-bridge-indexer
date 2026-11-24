.ONESHELL:
.DEFAULT_GOAL: all

py := uv run

source_dir := .
unit_tests_dir := tests/unit

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

docker-test:
	tests/e2e/smoke_test.sh $(if $(filter 1,$(BOOT)),--boot)

prod-check:
	tests/e2e/run_all.sh $(if $(filter 1,$(BOOT)),--boot)

update:         ## Update dependencies
	dipdup self update -q
	uv sync --all-extras --all-groups --link-mode symlink -U

STAND_ENV := tests/stand/tezosx.env
CASE_DIR   = tests/stand/cases/$(CASE)
STAND_ARGS = -e $(STAND_ENV) -e $(CASE_DIR)/window.env -c $(CASE_DIR)/config.yaml

_require-case:
	@test -n "$(CASE)" || (echo "usage: make $(MAKECMDGOALS) CASE=<name>  (see tests/stand/cases/)"; exit 1)
	@test -d "$(CASE_DIR)" || (echo "no such case: $(CASE_DIR)"; exit 1)

check-test-config: _require-case
	$(py) dipdup $(STAND_ARGS) config export --unsafe > /dev/null
	@echo "Test config OK: $(CASE)"

test-indexer: _require-case
	set -a; . $(CASE_DIR)/window.env; set +a
	rm -f "$${SQLITE_PATH:?set SQLITE_PATH in $(CASE_DIR)/window.env}"
	$(py) dipdup $(STAND_ARGS) run

inspect-test: _require-case
	set -a; . $(CASE_DIR)/window.env; set +a
	$(py) python -m tests.stand.cases.$(CASE).verify "$${SQLITE_PATH}"

PERF_PG := bridge-perf-pg
PERF_PG_PORT := 55432
PERF_DB_URL := postgres://dipdup:perf@127.0.0.1:$(PERF_PG_PORT)/dipdup

perf-db:
	@docker inspect -f '{{.State.Running}}' $(PERF_PG) 2>/dev/null | grep -q true && exit 0
	docker rm -f $(PERF_PG) >/dev/null 2>&1 || true
	docker run -d --name $(PERF_PG) --tmpfs /var/lib/postgresql/data \
		-e POSTGRES_USER=dipdup -e POSTGRES_PASSWORD=perf -e POSTGRES_DB=dipdup \
		-p $(PERF_PG_PORT):5432 postgres:17 >/dev/null
	@for i in $$(seq 1 60); do docker exec $(PERF_PG) pg_isready -U dipdup >/dev/null 2>&1 && exit 0; sleep 1; done; \
		echo "perf postgres did not come up"; exit 1

perf-db-down:
	docker rm -f $(PERF_PG) >/dev/null 2>&1 || true

bench-matcher: perf-db
	PYTHONPATH=. TEST_DB_URL=$(PERF_DB_URL) $(py) python -m tests.perf.bench_matcher \
		--scale $(or $(SCALE),1.0) --passes $(or $(PASSES),3) \
		$(if $(LABEL),--label $(LABEL)) $(if $(OUT),--out $(OUT))

test-pg: perf-db
	PYTHONPATH=. TEST_DB_URL=$(PERF_DB_URL) $(py) pytest $(unit_tests_dir)
