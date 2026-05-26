# Etherlink Bridge Indexer

DipDup indexer that tracks bridge operations between Tezos (L1) and Etherlink (L2).

The DipDup package lives at the repository root: `dipdup.yaml` is the base config and
`configs/<network>.yaml` are the per-network overlays (`mainnet`, `ghostnet`, `quebecnet`,
`rainbownet`, `shadownet`, `tezosx-shadownet`). The indexer is configured entirely through
environment variables referenced by those files (e.g. `NETWORK`, `TZKT_URL`,
`TEZOS_NODE_URL`, `ETHERLINK_NODE_URL`, `ROLLUP_NODE_URL`, `HASURA_URL`, `SMART_ROLLUP_ADDRESS`, …).

## Local setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```shell
make install                 # uv sync --all-extras --all-groups
make up                      # start postgres + hasura (docker compose)
```

Export the variables for your target network (or source a network env file), then run the
indexer with the base config plus the matching overlay:

```shell
uv run dipdup -c . -c configs/<network>.yaml run
```

`make run` is a shortcut for `uv run dipdup -c . run` (base config only). To validate a merged
config without starting the indexer:

```shell
make check-config NET=<network> ENV=<path-to-env-file>
```

## Development

```shell
make test                    # PYTHONPATH=. uv run pytest tests/
make lint                    # black, ruff, mypy
```

## Docker

```shell
docker buildx build . -t rollup-bridge-indexer
```

The image is published to GHCR by CI on every branch push and tag. At runtime the package,
`dipdup.yaml` and `configs/` are at `/opt/app`; pass the config flags via the container command,
e.g. `-c /opt/app/dipdup.yaml -c /opt/app/configs/<network>.yaml run`.
