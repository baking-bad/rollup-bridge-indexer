from bridge_indexer.types.output_proof.unpacker import BaseBinarySchema


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


class InodeTree2(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 4, 'int32'),
        ('Unnamed field 1', None, 'X11'),
    ]


class InodeTree3(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 8, 'int64'),
        ('Unnamed field 1', None, 'X11'),
    ]


class InodeTree128(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
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


class InodeTree208(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 1, 'uint8'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class InodeTree209(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 2, 'uint16'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class InodeTree210(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 4, 'int32'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class InodeTree211(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
        ('Unnamed field 0', 8, 'int64'),
        ('Unnamed field 1', None, 'X0'),
        ('Unnamed field 2', None, 'InodeTree'),
    ]


class InodeTree224(BaseBinarySchema):
    _schema = [
        ('Tag', 1, 'uint8'),
    ]


class InodeTree(BaseBinarySchema):
    _tag = True
