import sys
from collections import OrderedDict
from typing import Any
from typing import Type

from pydantic import BaseModel

from tezos_indexer.types.output_proof.decoder import decode


class Part(BaseModel):
    name: str
    size: int | str | None
    type: Type['BaseBinarySchema'] | str


class BaseBinarySchema:
    _schema: list[tuple]
    _tag: bool = False
    _tag_map: dict

    @property
    def buffer(self):
        return self._packed

    def __init__(self, value: bytes):
        self._packed: bytes = value
        self._unpacked = OrderedDict()
        self._size: int = 0

    @staticmethod
    def _import(type_name):
        for module_name in [
            'tezos_indexer.types.output_proof.inode_tree',
            'tezos_indexer.types.output_proof.tree_encoding',
            'tezos_indexer.types.output_proof.x_n',
            'tezos_indexer.types.output_proof.micheline_expression',
            'tezos_indexer.types.output_proof.primitive',
            'tezos_indexer.types.output_proof.output_proof',
            __name__,
        ]:
            if module_name not in sys.modules:
                __import__(module_name)
            try:
                return getattr(sys.modules[module_name], type_name)
            except (AttributeError):
                pass
        raise ImportError(type_name)

    def _handle_tag(self):
        tag = decode(['uint8'], self._packed)[0]
        type_name = self.__class__.__name__
        if type_name[-1].isdigit():
            tag = f'v{tag}'
        subtype_name = self.__class__.__name__ + str(tag)
        subtype = self._import(subtype_name)
        part_schema = subtype(self._packed)
        return part_schema.unpack()

    def unpack(self):
        if self._tag:
            unpacked_part, part_size = self._handle_tag()
            return unpacked_part, part_size

        for part in self._schema:
            part = Part(
                name=part[0],
                size=part[1],
                type=part[2],
            )
            if isinstance(part.size, str) and part.size[0] == '&':
                key = part.size[1:]
                part.size = self._unpacked[key]
                if isinstance(part.type, str) and part.type[0].islower() and not part.type[-1].isdigit():
                    part.type += str(part.size)

            if part.size:
                packed_part = self._packed[:part.size]
            else:
                packed_part = self._packed

            if isinstance(part.type, str) and part.type[0].islower():
                unpacked_part = decode([part.type], packed_part)[0]
                part_size = part.size
            else:
                if isinstance(part.type, str):
                    part.type = self._import(part.type)
                part_schema = part.type(value=packed_part)
                unpacked_part, part_size = part_schema.unpack()
                del part_schema
            self._unpacked[part.name] = unpacked_part

            self._packed = self._packed[part_size:]
            self._size += part_size
            self._handle_field_processed(name=part.name, value=unpacked_part)
        return self._unpacked, self._size

    def _handle_field_processed(self, name: str, value: Any):
        pass
