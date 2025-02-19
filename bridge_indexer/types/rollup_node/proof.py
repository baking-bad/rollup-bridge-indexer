from typing import TYPE_CHECKING

from base58 import b58decode_check
from base58 import b58encode_check
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Generator

    CallableGenerator = Generator[Callable, None, None]


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
