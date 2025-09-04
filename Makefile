.ONESHELL:
.DEFAULT_GOAL: all

py := uv run

source_dir := bridge_indexer
unit_tests_dir := tests

dipdup_args := -c .

test:
	PYTHONPATH=. $(py) pytest tests/

install:
	poetry install `if [ "${DEV}" = "0" ]; then echo "--only main"; fi` --sync

black:
	$(py) black $(source_dir) $(unit_tests_dir)

ruff:
	$(py) ruff check --fix-only --show-fixes $(source_dir) $(unit_tests_dir)

mypy:
	$(py) mypy $(source_dir) $(unit_tests_dir)

lint: black ruff mypy

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
