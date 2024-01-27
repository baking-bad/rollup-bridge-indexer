from typing import Any

from tezos_indexer.types.output_proof.unpacker import BaseBinarySchema
from vendor.pytezos.src.pytezos.michelson.forge import unforge_address


class MichelineExpression(BaseBinarySchema):
    _tag = True


class MichelineExpression0(BaseBinarySchema):
    _schema = [
        ('Int (tag 0)', 1, 'uint8'),
        ('int', None, 'Zarith'),
    ]


class MichelineExpression1(BaseBinarySchema):
    _schema = [
        ('String (tag 1)', 1, 'uint8'),
        ('size_of_string', 4, 'uint32'),
        ('string', '&size_of_string', 'hex'),
    ]


class MichelineExpression2(BaseBinarySchema):
    _schema = [
        ('Sequence (tag 2)', 1, 'uint8'),
        ('size_of_sequence', 4, 'uint32'),
        ('sequence', '&size_of_sequence', 'MichelineExpression'),
    ]


class MichelineExpression3(BaseBinarySchema):
    _schema = [
        ('Prim__no_args__no_annots (tag 3)', 1, 'uint8'),
        ('prim', 1, 'Primitive'),
    ]


class MichelineExpression4(BaseBinarySchema):
    _schema = [
        ('Prim__no_args__some_annots (tag 4)', 1, 'uint8'),
        ('prim', 1, 'Primitive'),
        ('size_of_annots', 4, 'uint32'),
        ('annots', '&size_of_annots', 'hex'),
    ]


class MichelineExpression5(BaseBinarySchema):
    _schema = [
        ('Prim__1_arg__no_annots (tag 5)', 1, 'uint8'),
        ('prim', 1, 'Primitive'),
        ('arg', None, 'MichelineExpression'),
    ]


class MichelineExpression6(BaseBinarySchema):
    _schema = [
        ('Prim__1_arg__some_annots (tag 6)', 1, 'uint8'),
        ('prim', 1, 'Primitive'),
        ('arg', None, 'MichelineExpression'),
        ('size_of_annots', 4, 'uint32'),
        ('annots', '&size_of_annots', 'hex'),
    ]


class MichelineExpression7(BaseBinarySchema):
    _schema = [
        ('Prim__2_args__no_annots (tag 7)', 1, 'uint8'),
        ('prim', 1, 'Primitive'),
        ('arg1', None, 'MichelineExpression'),
        ('arg2', None, 'MichelineExpression'),
    ]


class MichelineExpression8(BaseBinarySchema):
    _schema = [
        ('Prim__2_args__some_annots (tag 8)', 1, 'uint8'),
        ('prim', 1, 'Primitive'),
        ('arg1', None, 'MichelineExpression'),
        ('arg2', None, 'MichelineExpression'),
        ('size_of_annots', 4, 'uint32'),
        ('annots', '&size_of_annots', 'hex'),
    ]


class MichelineExpression9(BaseBinarySchema):
    _schema = [
        ('Prim__generic (tag 9)', 1, 'uint8'),
        ('prim', 1, 'Primitive'),
        ('size_of_args', 4, 'uint32'),
        ('arg1', '&size_of_args', 'MichelineExpression'),
        ('size_of_annots', 4, 'uint32'),
        ('annots', '&size_of_annots', 'hex'),
    ]


class MichelineExpression10(BaseBinarySchema):
    _schema = [
        ('Bytes (tag 10)', 1, 'uint8'),
        ('size_of_bytes', 4, 'uint32'),
        ('bytes', '&size_of_bytes', 'hex'),
    ]


class Originated(BaseBinarySchema):
    def unpack(self):
        return unforge_address(self._packed), len(self._packed)


class Zarith(BaseBinarySchema):
    def unpack(self):
        bits_array = []
        is_positive: bool | None = None
        for int_val in self._packed:
            self._size += 1
            bits: str = f'{bin(int_val).removeprefix("0b"):0>8}'
            leading_bit, bits = bits[0], bits[1:]
            if is_positive is None:
                sign_bit, bits = bits[0], bits[1:]
                is_positive = sign_bit == '0'
            bits_array = [bits] + bits_array
            if leading_bit == '0':
                break

        bits_array = ''.join(bits_array)
        return int(bits_array, 2), self._size


class OutputProofOutput(BaseBinarySchema):
    _schema = [
        ('outbox_level', 4, 'int32'),
        ('message_index', None, 'Zarith'),
        ('message', None, 'Message'),
    ]


class OutboxMessage(BaseBinarySchema):
    _schema = [
        ('size_of_message', 4, 'uint32'),
        ('message', '&size_of_message', 'Message'),
    ]


class Transaction(BaseBinarySchema):
    _schema = [
        ('parameters', None, 'MichelineExpression'),
        ('destination', 22, 'Originated'),
        ('size_of_entrypoint', 4, 'uint32'),
        ('entrypoint', '&size_of_entrypoint', 'hex'),
    ]

    def _handle_field_processed(self, name: str, value: Any):
        if name == 'entrypoint':
            assert self


class TransactionsSequence(BaseBinarySchema):
    def unpack(self):
        self._unpacked: list[Transaction] = []
        while len(self._packed):
            unpacked_item, item_size = Transaction(self._packed).unpack()
            self._size += item_size
            self._unpacked.append(unpacked_item)
            self._packed = self._packed[item_size:]

        return self._unpacked, self._size
