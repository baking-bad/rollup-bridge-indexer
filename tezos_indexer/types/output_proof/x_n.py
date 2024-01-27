from typing import Any

from tezos_indexer.types.output_proof.tree_encoding import TreeEncoding
from tezos_indexer.types.output_proof.unpacker import BaseBinarySchema


class OutputProof0(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'int16'),
        ('Unnamed field 1', 32, 'hex32'),
        ('Unnamed field 2', 32, 'hex32'),
        ('Unnamed field 3', None, TreeEncoding),
    ]


class OutputProof1(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'int16'),
        ('Unnamed field 1', 32, 'hex32'),
        ('Unnamed field 2', 32, 'hex32'),
        ('Unnamed field 3', None, TreeEncoding),
    ]


class OutputProof2(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'int16'),
        ('Unnamed field 1', 32, 'hex32'),
        ('Unnamed field 2', 32, 'hex32'),
        ('Unnamed field 3', None, TreeEncoding),
    ]


class OutputProof3(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'int16'),
        ('Unnamed field 1', 32, 'hex32'),
        ('Unnamed field 2', 32, 'hex32'),
        ('Unnamed field 3', None, TreeEncoding),
    ]


class OutputProof(BaseBinarySchema):
    _tag = True


class X6(BaseBinarySchema):
    _schema = [
        ('size_1', 1, 'uint8'),
        ('Unnamed field 0', '&size_1', 'hex'),
    ]


class X5(BaseBinarySchema):
    _schema = [
        ('Unnamed field 0', None, X6),
        ('Unnamed field 1', None, 'TreeEncoding'),
    ]


class X11(BaseBinarySchema):
    _schema = [
        ('Unnamed field 0', None, 'InodeTree'),
        ('Unnamed field 1', None, 'InodeTree'),
    ]



class Message0(BaseBinarySchema):
    _schema = [
        ('Atomic_transaction_batch (tag 0)', 1, 'uint8'),
        ('size_of_transactions', 4, 'uint32'),
        ('transactions', '&size_of_transactions', 'Transaction'),
    ]
    def unpack(self):
        return super().unpack()

class Message(BaseBinarySchema):
    _tag = True

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


