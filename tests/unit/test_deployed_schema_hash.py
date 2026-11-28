from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOYED_POSTGRES_SCHEMA_HASH = '85712b1165721443b22fe02062ead08e1eafaf705e0dfdb35f37b3223b1e1976'
PRINT_SCHEMA_HASH_AS_A_BOOT_GENERATES_IT = '''
import asyncio

from dipdup.database import get_connection
from dipdup.database import get_schema_hash
from dipdup.database import get_tortoise_config
from dipdup.database import prepare_models
from tortoise import Tortoise


async def main() -> None:
    prepare_models('rollup_bridge_indexer')
    await Tortoise.init(config=get_tortoise_config('postgres://dipdup:unused@127.0.0.1:1/dipdup', 'rollup_bridge_indexer'))
    print(get_schema_hash(get_connection()))
    await Tortoise.close_connections()


asyncio.run(main())
'''


def test_the_models_generate_the_postgres_schema_that_deployed_databases_hold() -> None:
    schema_hash = subprocess.run(
        [sys.executable, '-c', PRINT_SCHEMA_HASH_AS_A_BOOT_GENERATES_IT],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert schema_hash == DEPLOYED_POSTGRES_SCHEMA_HASH, (
        f'The Postgres DDL generated from the models hashes to {schema_hash}, '
        f'but deployed databases hold {DEPLOYED_POSTGRES_SCHEMA_HASH}. '
        'With `advanced.reindex.schema_modified: exception` this image refuses to boot on every one of them '
        '(ReindexingRequiredError, "Schema hash mismatch"), and that failed boot flags the database so the previous image refuses too. '
        'Every DDL line counts: a column, an index, a table or column COMMENT that Tortoise derives from a model docstring '
        'or a field `description=`, and a DipDup or Tortoise upgrade that changes what they generate. '
        'Either restore the DDL (diff `tortoise.utils.get_schema_sql` before and after the change), '
        'or ship it deliberately as a reindex or the two-boot hash adoption under "Shipping changes" in CLAUDE.md '
        'and set this pin to the new hash in the same change.'
    )
