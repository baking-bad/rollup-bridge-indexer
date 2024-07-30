from bridge_indexer.types.kernel.evm_events.withdrawal import WithdrawalPayload


class TestTezosTypes:
    def test_forged_tezos_account(self):
        data = {
            'ticket_hash': 60615985060701456055301886909922301002429638263192082348116248956912036664274,
            'sender': '0xbefd2c6ffc36249ebebd21d6df6376ecf3bac448',
            'ticket_owner': '0x87dcbf128677ba36e79d47daf4eb4e51610e0150',
            'receiver': b'\x00\x00V\xd8-\xf0\x00\xedi\xff2>\x02<^\xe2\x00#\xd7\x16C\x11',
            'proxy': b'\x01\xca\xac\xcb\x87M\x9d\xb7\xb5M6\xa5/al\x1bF\xe0\xfb\x85_\x00',
            'amount': 10,
            'withdrawal_id': 0,
        }
        dto = WithdrawalPayload.model_validate(data)
        assert dto.amount == 10
        assert dto.receiver == 'tz1TZDn2ZK35UnEjyuGQRVeM2NC5tQScJLpQ'
        assert dto.proxy == 'KT1T4R4XNpbaNtGAJSukr3a5Wd4UUQ7yCGK1'
