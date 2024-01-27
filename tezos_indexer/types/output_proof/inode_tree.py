from tezos_indexer.types.output_proof.unpacker import BaseBinarySchema


class InodeTree0(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 1, 'uint8'),
        ('Unnamed field 1', None, 'X11'),
    ]

class InodeTree1(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'uint16'),
        ('Unnamed field 1', None, 'X11'),
    ]

class InodeTree129(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', None, 'X5'),
    ]

class InodeTree130(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0[0]', None, 'X5'),
        ('Unnamed field 0[1]', None, 'X5'),
    ]


class InodeTree192(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Context_hash', 32, 'hex32'),
    ]

class InodeTree224(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
    ]


class InodeTree(BaseBinarySchema):
    _tag = True
