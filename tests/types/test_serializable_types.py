from datetime import UTC
from datetime import datetime

from asyncpg.pgproto import pgproto

from bridge_indexer.models import RollupOutboxMessage


class TestSerializableTypes:
    def test_asyncpg_uuid_encoder(self):
        data = {
            'l1_transaction_id': None,
            'updated_at': datetime(2025, 4, 16, 14, 25, 30, 917463, tzinfo=UTC),
            'outbox_message_id': pgproto.UUID('efdcd742-c789-404c-b863-7f50bc786272'),
        }

        dump = RollupOutboxMessage._meta.fields_map['message'].to_db_value(data, RollupOutboxMessage)

        assert isinstance(dump, str)
        assert data['outbox_message_id'].hex in dump
