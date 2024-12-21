from typing import ClassVar

from bridge_indexer.types.output_proof.unpacker import BaseBinarySchema


class TreeEncoding0(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 1, 'uint8'),
        ('Unnamed field 1', None, 'X11'),
    ]


class TreeEncoding1(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'uint16'),
        ('Unnamed field 1', None, 'X11'),
    ]


class TreeEncoding2(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 4, 'int32'),
        ('Unnamed field 1', None, 'X11'),
    ]


class TreeEncoding3(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 8, 'int64'),
        ('Unnamed field 1', None, 'X11'),
    ]


class TreeEncoding128(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
    ]


class TreeEncoding129(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', None, 'X5'),
    ]


class TreeEncoding130(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0[0]', None, 'X5'),
        ('Unnamed field 0[1]', None, 'X5'),
    ]


# class TreeEncoding131(BaseBinarySchema):
#     _schema: ClassVar[list[tuple]] = [         ('Tag', 1, 'uint8'),
#         (' # bytes in next field', 4, 'uint30'),
#         ('Unnamed field 0', None, 'X5'),  # sequence
#     ]


class TreeEncoding192(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('size_2', 1, 'uint8'),
        ('Unnamed field 0', '&size_2', 'hex'),
    ]


class TreeEncoding193(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('size_2', 2, 'uint16'),
        ('Unnamed field 0', '&size_2', 'hex'),
    ]


class TreeEncoding195(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('size_2', 4, 'uint32'),
        ('Unnamed field 0', '&size_2', 'hex'),
    ]


class TreeEncoding200(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Context_hash', 32, 'hex'),
    ]


class TreeEncoding208(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Context_hash', 32, 'hex32'),
    ]


class TreeEncoding216(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 1, 'uint8'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class TreeEncoding217(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'uint16'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class TreeEncoding218(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 4, 'int32'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class TreeEncoding219(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 8, 'int64'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class TreeEncoding224(BaseBinarySchema):
    _schema: ClassVar[list[tuple]] = [
        ('Tag', 1, 'uint8'),
    ]


class TreeEncoding(BaseBinarySchema):
    _tag = True
