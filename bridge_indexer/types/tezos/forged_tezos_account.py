from pytezos.michelson.forge import unforge_address


# OriginatedAccount = SmartContractAccount | SmartRollupAccount
# Account = ImplicitAccount | OriginatedAccount
class ForgedTezosAccount:

    def __init__(self, value: bytes):
        self.value = value
        self._base58 = None

    @classmethod
    def validate(cls, value):
        return unforge_address(value)

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    def __repr__(self):
        return f'Account<{self._base58}>'
