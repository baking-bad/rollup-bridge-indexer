from pytezos.michelson.forge import unforge_address


class ForgedTezosAccount:

    def __init__(self, value: bytes | str):
        self.value = value
        self._base58 = None

    @classmethod
    def validate(cls, value, *args):
        # Sources of `value`:
        #   - base58 str from pytezos `MichelsonType.to_python_object()` when decoding
        #     `default %withdraw` outbox-message parameters (ticketer/receiver in
        #     `types/ticketer/tezos_parameters/withdraw.py`)
        #   - raw `bytes` from EVM `bytes22` event fields (receiver/proxy in
        #     `kernel.Withdrawal`, `kernel_native.Withdrawal`, `kernel_native.FastWithdrawal`)
        # Output: base58 str written to `l1_withdrawal.l1_account` and used inside
        # `OutboxParametersHash` to build the hash that matches outbox <-> l2_withdrawal.
        if isinstance(value, str):
            if value[:3] in ['KT1', 'tz1', 'tz2', 'tz3', 'tz4', 'sr1']:
                return value
            value = bytes.fromhex(value.removeprefix('0x'))
        return unforge_address(value)

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    def __repr__(self):
        return f'Account<{self._base58}>'
