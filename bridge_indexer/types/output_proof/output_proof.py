from __future__ import absolute_import

from bridge_indexer.types.output_proof.tree_encoding import TreeEncoding
from bridge_indexer.types.output_proof.unpacker import BaseBinarySchema


class OutputProofData(BaseBinarySchema):
    _schema = [
        ('output_proof', None, 'OutputProof'),
        ('output_proof_output', None, 'OutputProofOutput'),
    ]


class OutputProof0(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Version', 2, 'int16'),
        ('Before', 32, 'hex32'),
        ('After', 32, 'hex32'),
        ('State', None, TreeEncoding),
    ]


class OutputProof1(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Version', 2, 'int16'),
        ('Before', 32, 'hex32'),
        ('After', 32, 'hex32'),
        ('State', None, TreeEncoding),
    ]


class OutputProof2(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Version', 2, 'int16'),
        ('Before', 32, 'hex32'),
        ('After', 32, 'hex32'),
        ('State', None, TreeEncoding),
    ]


class OutputProof3(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Version', 2, 'int16'),
        ('Before', 32, 'hex32'),
        ('After', 32, 'hex32'),
        ('State', None, TreeEncoding),
    ]


class OutputProof(BaseBinarySchema):
    _tag = True


class Message0(BaseBinarySchema):
    _schema = [
        ('Atomic_transaction_batch (tag 0)', 1, 'uint8'),
        ('size_of_transactions', 4, 'uint32'),
        ('transactions', '&size_of_transactions', 'TransactionsSequence'),
    ]


class Message(BaseBinarySchema):
    _tag = True


class Boolean1(BaseBinarySchema):
    map = {
        b'\x00': False,
        b'\xff': True,
    }

    def unpack(self):
        print(len(self._packed))
        return self.map[self._packed[:1]], 1


class Proof(BaseBinarySchema):
    _schema = [
        ('size_of_pvm_step', 4, 'uint32'),
        ('pvm_step', '&size_of_pvm_step', 'hex'),
        # ('presence_of_input_proof', 1, 'Boolean1'),
        # ('input_proof', None, 'X2'),
    ]


class X2v0(BaseBinarySchema):
    _schema = [
        ('inbox proof (tag 0)', 1, 'uint8'),
        ('level', 4, 'int32'),
        ('message_counter', None, 'Zarith'),
        ('size_of_serialized_proof', 4, 'uint32'),
        ('serialized_proof', '&size_of_serialized_proof', 'hex'),
    ]


class X2v1(BaseBinarySchema):
    _schema = [
        ('reveal proof (tag 1)', 1, 'uint8'),
        ('reveal_proof', None, 'X1'),
    ]


class X2v2(BaseBinarySchema):
    _schema = [
        ('first input (tag 2)', 1, 'uint8'),
    ]


class X2(BaseBinarySchema):
    _tag = True
