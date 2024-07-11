# Etherlink Bridge Indexer
DipDup indexer for tracking Bridge operations.
## Setup with docker
#### Build docker image
* `docker build . -t etherlink-bridge-indexer`
#### Setup environment
* copy example file: `cp .env.dist .env` and replace sample values with your settings
* edit `bridge_indexer/dipdup.yaml` if required
#### Run with compose
* `docker compose up -d` 
## Local setup
#### Prepare virtual environment and install dependencies
```shell
poetry shell
poetry install --sync --no-root
```
#### Setup environment
* copy example file: `cp .env.dist .env` and replace sample values with your settings
* edit `bridge_indexer/dipdup.yaml` if required
#### Run as python module
`python -m dipdup -c . run`
