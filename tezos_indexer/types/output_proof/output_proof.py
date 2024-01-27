from __future__ import absolute_import

from tezos_indexer.types.output_proof.tree_encoding import TreeEncoding
from tezos_indexer.types.output_proof.unpacker import BaseBinarySchema


class OutputProofData(BaseBinarySchema):
    _schema = [
        ('output_proof', None, 'OutputProof'),
        ('output_proof_state', 32, 'hex32'),
        ('output_proof_output', None, 'OutputProofOutput'),
    ]


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


class Message0(BaseBinarySchema):
    _schema = [
        ('Atomic_transaction_batch (tag 0)', 1, 'uint8'),
        ('size_of_transactions', 4, 'uint32'),
        ('transactions', '&size_of_transactions', 'TransactionsSequence'),
    ]

    def unpack(self):
        return super().unpack()


class Message(BaseBinarySchema):
    _tag = True
