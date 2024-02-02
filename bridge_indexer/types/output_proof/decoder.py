# from eth_abi import decode, encode
# from eth_abi.decoding import StringDecoder
# from eth_abi.encoding import TextStringEncoder
from eth_abi.base import parse_type_str
from eth_abi.codec import ABICodec
from eth_abi.decoding import SignedIntegerDecoder
from eth_abi.decoding import SingleDecoder
from eth_abi.decoding import UnsignedIntegerDecoder
from eth_abi.registry import ABIRegistry
from eth_abi.registry import BaseEquals


class FixedUnsignedIntegerDecoder(UnsignedIntegerDecoder):
    @parse_type_str('uint')
    def from_type_str(cls, abi_type, registry):
        assert abi_type
        return cls(value_bit_size=abi_type.sub, data_byte_size=abi_type.sub // 8)


class FixedSignedIntegerDecoder(SignedIntegerDecoder):
    @parse_type_str('int')
    def from_type_str(cls, abi_type, registry):
        assert abi_type
        return cls(value_bit_size=abi_type.sub, data_byte_size=abi_type.sub // 8)


class BytesToTextDecoder(SingleDecoder):
    size: int

    def __init__(self, size, **kwargs):
        super().__init__(**kwargs)
        self.size = size

    def read_data_from_stream(self, stream):
        data_length = self.size
        data = stream.read(data_length)

        return data[:data_length]

    def validate_padding_bytes(self, value, padding_bytes):
        return

    @staticmethod
    def decoder_fn(data: bytes):
        try:
            result = data.decode('ascii')
            if len(result) == len(data):
                return result
        except UnicodeDecodeError:
            pass

        try:
            assert len(data) <= 4
            return int.from_bytes(data, 'big')
        except (UnicodeDecodeError, AssertionError):
            pass

        return '0x' + data.hex()

    @parse_type_str('hex')
    def from_type_str(cls, abi_type, registry):
        return cls(size=abi_type.sub)


registry = ABIRegistry()

registry.register_decoder(
    lookup=BaseEquals(base='uint', with_sub=True),
    decoder=FixedUnsignedIntegerDecoder,
)
registry.register_decoder(
    lookup=BaseEquals(base='int', with_sub=True),
    decoder=FixedSignedIntegerDecoder,
)
registry.register_decoder(
    lookup=BaseEquals(base='hex', with_sub=True),
    decoder=BytesToTextDecoder,
)
# registry.register_decoder(
#     lookup=BaseEquals(base='hex'),
#     decoder=BytesToHexDecoder,
# )

default_codec = ABICodec(registry)
decode = default_codec.decode
