import json
from typing import TYPE_CHECKING

from base58 import b58decode_check
from base58 import b58encode_check
from pydantic import BaseModel

if TYPE_CHECKING:
    from typing import Generator

    from pydantic.typing import AnyCallable

    CallableGenerator = Generator[AnyCallable, None, None]


class SmartRollupCommitmentHash:
    __slots__ = '_b58encoded', '_forged'

    _prefix_decoded = 'src1'
    _prefix_encoded = b'\x11\xa5\x86\x8a'
    _length = 54

    def _init_from_b58encoded(self):
        self._forged = b58decode_check(self._b58encoded)[len(self._prefix_decoded) :]

    def _b58encoded_resolver(self, value):
        if not isinstance(value, str):
            raise ValueError
        if not value.startswith(self._prefix_decoded):
            raise ValueError
        if len(value) != self._length:
            raise ValueError

        self._b58encoded = value
        self._init_from_b58encoded()
        # del self.__class__._prefix_decoded
        # del self.__class__._prefix_encoded
        # del self.__class__._length

    def __resolve_value(self, value):
        for resolver in [
            self._b58encoded_resolver,
            # self.init_from_bytes_validator,
            # self.init_from_hex_validator,
            # self.init_from_hex_with_prefix_validator,
        ]:
            try:
                return resolver(value)
            except ValueError:
                continue
        raise ValueError

    def __init__(self, value: str | bytes):
        # self.__value = value
        self._b58encoded: str
        self._forged: bytes
        self.__resolve_value(value)

    def _init_from_forged(self):
        self._b58encoded = b58encode_check(self._prefix_encoded + self._forged)

    @classmethod
    def __get_validators__(cls) -> 'CallableGenerator':
        yield cls.validator

    @classmethod
    def validator(cls, value):
        return cls(value)

    def __repr__(self):
        return f"<SmartRollupCommitmentHash '{self._b58encoded}'>"

    def __str__(self):
        return self._b58encoded


class Proof(str):
    pass


class OutputProof(BaseModel):
    commitment: SmartRollupCommitmentHash
    proof: Proof


data = '{"commitment":"src139Y6mMHjFv1sLG8hAx9SP4sNT8Wz52DRZSos5gZqBxJUcS8cj4","proof":"03000248499c9d41a8ad43af5c2143d08d3046c0fcda8c0ccca35a493c62d9a71b2cb448499c9d41a8ad43af5c2143d08d3046c0fcda8c0ccca35a493c62d9a71b2cb40005820764757261626c65d059a01a146cc0254fb559ac85e2bcc1e6e51ccde65f6bda09869f4e86cbf442a903746167c00800000004536f6d650003c08cd439daf609f838d2c8a43aa75ed227baaaca7f4a9312448959d2641896f5b6820576616c7565810370766d8107627566666572738205696e707574820468656164c00100066c656e677468c00100066f75747075740004820132810a6c6173745f6c6576656cc004002d42a70133810f76616c69646974795f706572696f64c00400013b0082013181086f7574626f78657301c184015f5d012fa2c09e9d0fb1f84ba48fc95ba3b90a9404cc8e31e3cb434601b0fb23d45d39fbf6a301179a010bfac0c699abb5e9b26ce388c58d1712bd11b79d7ac52d2c5d296578fc0cea8dc4685c010609c0713ac44b458e045d6ba3fd64685bfa03f512c655fafea361d8ae2909b70cd33e010302c0ef8a57be3c044cc0852c87c9c0c0a818649935c58f48e63e659766ba9a1539c601018fc0730f6f5c72cf675562c3808fbb1a8c7d6d77cc9a3457c7c7e89295b5f728ead500dcc0762d69ead8181946282f52bde668ab7789d5538a99ea660a22ccc6f2fa3516b8006f003d0024c063cff88ca82e2c6e0b5cdfea29819148427f4f86cb3a791f2e45822a880f01360018c0f6913ff706bd5195c84a8f0bce104d258e86f5292ddd86ac4835844c24c48fae000d00070003c0a301ecbe3b8643e33f5462e08aa448a77d81ab4450b819b5ba8dcbdae347304b8107323931393133360003810468656164c001008208636f6e74656e74730003820130c0e8000000e400000000df07070a0000001600008a7390072a389159c73687165cd7910e8a39160607070a000000160155c34fe2c664715ec4c0a43a01f1e4141fa9b3cc0007070707002a05090a0000007405020000006e07040100000010636f6e74726163745f616464726573730a0000001c050a000000160158827df9def8a4d2c92152c872c553b01ff3de0b0007040100000008746f6b656e5f69640a0000000305002a0704010000000a746f6b656e5f747970650a0000000905010000000346413200050155c34fe2c664715ec4c0a43a01f1e4141fa9b3cc000000000877697468647261770132c0e8000000e400000000df07070a0000001600008a7390072a389159c73687165cd7910e8a39160607070a000000160155c34fe2c664715ec4c0a43a01f1e4141fa9b3cc0007070707002a05090a0000007405020000006e07040100000010636f6e74726163745f616464726573730a0000001c050a000000160158827df9def8a4d2c92152c872c553b01ff3de0b0007040100000008746f6b656e5f69640a0000000305002a0704010000000a746f6b656e5f747970650a0000000905010000000346413200050155c34fe2c664715ec4c0a43a01f1e4141fa9b3cc00000000087769746864726177c04c4bee8b261e5aa4de96a58dcb00a8c69a0125fcaed4e8844d1501776867852e066c656e677468c00103c09b2a388621c7631a1cb7ba04a2befdff5ba9411eee89f2e97b54e36ec3081a8cc0f16c32fc0389207e2a49d6c949950399d78cebfedd65ec1a63e208f626aa3036c085f8e370f9722a724dfa244ec6322c9dd23ebd31e914d1f796faf21e650f5f80c0883eae96313f9934c2b34264155923b34bd63ca3e421d5e566c90e2149490c78c095cf380de310d3eea4bbab05ffca1e5009bd6fe38318d68c57287ae4e013fd5cc0355175ef3f98242f1ecd6f9035c95a359b7795e8bf9124db7aaa4fb090e43054c0722759fdcd2027211b4a1d994562c1dd0d108e51106e52cd516b19040eb9dede0134810d6d6573736167655f6c696d6974c002a401047761736dd0c793e2bb253ac6f51153c363fe3acb12dd711522feb80c2c1ae125b427113f4e48499c9d41a8ad43af5c2143d08d3046c0fcda8c0ccca35a493c62d9a71b2cb4002c8ae00000000000df07070a0000001600008a7390072a389159c73687165cd7910e8a39160607070a000000160155c34fe2c664715ec4c0a43a01f1e4141fa9b3cc0007070707002a05090a0000007405020000006e07040100000010636f6e74726163745f616464726573730a0000001c050a000000160158827df9def8a4d2c92152c872c553b01ff3de0b0007040100000008746f6b656e5f69640a0000000305002a0704010000000a746f6b656e5f747970650a0000000905010000000346413200050155c34fe2c664715ec4c0a43a01f1e4141fa9b3cc00000000087769746864726177"}'

op = OutputProof.parse_obj(json.loads(data))
opc = op.commitment
assert opc
