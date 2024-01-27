from tezos_indexer.types.output_proof.unpacker import BaseBinarySchema


class Primitive0(BaseBinarySchema):
    def unpack(self):
        return 'parameter', 1


class Primitive1(BaseBinarySchema):
    def unpack(self):
        return 'storage', 1


class Primitive2(BaseBinarySchema):
    def unpack(self):
        return 'code', 1


class Primitive3(BaseBinarySchema):
    def unpack(self):
        return 'False', 1


class Primitive4(BaseBinarySchema):
    def unpack(self):
        return 'Elt', 1


class Primitive5(BaseBinarySchema):
    def unpack(self):
        return 'Left', 1


class Primitive6(BaseBinarySchema):
    def unpack(self):
        return 'None', 1


class Primitive7(BaseBinarySchema):
    def unpack(self):
        return 'Pair', 1


class Primitive8(BaseBinarySchema):
    def unpack(self):
        return 'Right', 1


class Primitive9(BaseBinarySchema):
    def unpack(self):
        return 'Some', 1


class Primitive10(BaseBinarySchema):
    def unpack(self):
        return 'True', 1


class Primitive11(BaseBinarySchema):
    def unpack(self):
        return 'Unit', 1


class Primitive(BaseBinarySchema):
    _tag = True
