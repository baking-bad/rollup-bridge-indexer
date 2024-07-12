from pytezos.michelson.forge import unforge_address


class ForgedTezosAccount:

    def __init__(self, value: bytes | str):
        self.value = value
        self._base58 = None

    @classmethod
    def validate(cls, value, *args):
        if isinstance(value, str):
            if value[:3] in ['KT1', 'tz1', 'tz2', 'tz3']:
                return value
            value = bytes.fromhex(value.removeprefix('0x'))
        return unforge_address(value)

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    def __repr__(self):
        return f'Account<{self._base58}>'
