from bridge_indexer.types.kernel_module.evm_events.withdrawal import Withdrawal


class TestTezosTypes:
    def test_forged_tezos_account(self):
        data = {
            'receiver': bytes.fromhex('01a6938a03cf1d7652a7b871bd9c7c36b4655fa80300'),
            'amount': 10,
        }
        dto = Withdrawal.parse_obj(data)
        assert dto.amount == 10
        assert dto.receiver == 'KT1PmYUomF3HDxsGWYQUCbLi2X8WvT7ZHv8o'
