from bridge_indexer.types.output_proof.unpacker import BaseBinarySchema


class X0(BaseBinarySchema):
    _schema = [
        ('size_1', 1, 'uint8'),
        ('Unnamed field 0', '&size_1', 'hex'),
    ]


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
