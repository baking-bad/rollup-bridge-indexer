from tezos_indexer.types.output_proof.unpacker import BaseBinarySchema


class TreeEncoding0(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 1, 'uint8'),
        ('Unnamed field 1', None, 'X11'),
    ]


class TreeEncoding1(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'uint16'),
        ('Unnamed field 1', None, 'X11'),
    ]


# class TreeEncoding_2(BaseBinarySchema):
#     _schema = [
#
#
# ('Tag',             1,               'uint8'),
# ('Unnamed field 0', 4,              'int32'),
# ('Unnamed field 1', None, $X_11                  |
#
#
# ]
# class TreeEncoding_3(BaseBinarySchema):
#     _schema = [
#
#
# ('Tag',             1,               'uint8'),
# ('Unnamed field 0', 8,              'int64'),
# ('Unnamed field 1', None, $X_11                  |
#
#
# ]
# class TreeEncoding_128(BaseBinarySchema):
#     _schema = [
#
# ('Tag',  1, 'uint8'),
#
#
#
# ]
class TreeEncoding129(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', None, 'X5'),
    ]


class TreeEncoding130(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0[0]', None, 'X5'),
        ('Unnamed field 0[1]', None, 'X5'),
    ]


# class TreeEncoding131(BaseBinarySchema):
#     _schema = [
#         ('Tag', 1, 'uint8'),
#         (' # bytes in next field', 4, 'uint30'),
#         ('Unnamed field 0', None, 'X5'),  # sequence
#     ]


class TreeEncoding192(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('size_2', 1, 'uint8'),
        ('Unnamed field 0', '&size_2', 'hex'),
    ]


# class TreeEncoding_193(BaseBinarySchema):
#     _schema = [
#
# ('Tag',                   1,   'uint8'),
# (' # bytes in next field', 2,  'uint16'),
# ('Unnamed field 0',       | Variable | bytes                   |
#
#
# ]class TreeEncoding_195(BaseBinarySchema):
#     _schema = [
#
# ('Tag',                   1,   'uint8'),
# (' # bytes in next field', 4,  | 'uint30' |
# ('Unnamed field 0',       | Variable | bytes                   |
#
#
# ]class TreeEncoding_200(BaseBinarySchema):
#     _schema = [
#
# ('Tag',          1,   'uint8'),
# (' Context_hash ', 32, ' | bytes                  |
#
#
# ]
class TreeEncoding208(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Context_hash ', 32, 'hex32'),
    ]


# class TreeEncoding_216(BaseBinarySchema):
#     _schema = [
#
#
# ('Tag',             1,               'uint8'),
# ('Unnamed field 0', 1,               'uint8'),
# ('Unnamed field 1', None, $X_0                   |
# ('Unnamed field 2', None, $inode_tree            |
#
#
# ]
# class TreeEncoding_217(BaseBinarySchema):
#     _schema = [
#
# ('Tag',             1,               'uint8'),
# ('Unnamed field 0', 2,              'uint16'),
# ('Unnamed field 1', None, $X_0                    |
# ('Unnamed field 2', None, $inode_tree             |
#
#
# ]
# class TreeEncoding_218(BaseBinarySchema):
#     _schema = [
#
#
# ('Tag',             1,               'uint8'),
# ('Unnamed field 0', 4,              'int32'),
# ('Unnamed field 1', None, $X_0                   |
# ('Unnamed field 2', None, $inode_tree            |
#
#
# ]
# class TreeEncoding_219(BaseBinarySchema):
#     _schema = [
#
#
# ('Tag',             1,               'uint8'),
# ('Unnamed field 0', 8,              'int64'),
# ('Unnamed field 1', None, $X_0                   |
# ('Unnamed field 2', None, $inode_tree            |
#
#
# ]
# class TreeEncoding_224(BaseBinarySchema):
#     _schema = [
#
# ('Tag',  1, 'uint8'),
# ]


class TreeEncoding(BaseBinarySchema):
    _tag = True
