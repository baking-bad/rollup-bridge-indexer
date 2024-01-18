import base58
from base58 import b58decode_check
from dipdup.codegen.evm_subsquid import _abi_type_map
from eth_abi.base import (
    parse_type_str,
)
from eth_abi.decoding import Fixed32ByteSizeDecoder
from eth_abi.registry import BaseEquals
from eth_abi.registry import encoding
from eth_abi.registry import registry


def tb(l):
    return b''.join(map(lambda x: x.to_bytes(1, 'big'), l))


base58_encodings = [
    #    Encoded   |               Decoded             |
    # prefix | len | prefix                      | len | Data type
    (b"B", 51, tb([1, 52]), 32, "block hash"),
    (b"o", 51, tb([5, 116]), 32, "operation hash"),
    (b"Lo", 52, tb([133, 233]), 32, "operation list hash"),
    (b"LLo", 53, tb([29, 159, 109]), 32, "operation list list hash"),
    (b"P", 51, tb([2, 170]), 32, "protocol hash"),
    (b"Co", 52, tb([79, 199]), 32, "context hash"),
    (b"tz1", 36, tb([6, 161, 159]), 20, "ed25519 public key hash"),
    (b"tz2", 36, tb([6, 161, 161]), 20, "secp256k1 public key hash"),
    (b"tz3", 36, tb([6, 161, 164]), 20, "p256 public key hash"),
    (b"tz4", 36, tb([6, 161, 16]), 20, "BLS-MinPk"),
    (b"KT1", 36, tb([2, 90, 121]), 20, "originated address"),
    (b"txr1", 37, tb([1, 128, 120, 31]), 20, "tx_rollup_l2_address"),
    (b"sr1", 36, tb([6, 124, 117]), 20, "address prefix for originated smart rollup"),
    (b"src1", 54, tb([17, 165, 134, 138]), 32, "address prefix for smart rollup commitment"),
    (b"id", 30, tb([153, 103]), 16, "cryptobox public key hash"),
    (b'expr', 54, tb([13, 44, 64, 27]), 32, u'script expression'),
    (b"edsk", 54, tb([13, 15, 58, 7]), 32, "ed25519 seed"),
    (b"edpk", 54, tb([13, 15, 37, 217]), 32, "ed25519 public key"),
    (b"spsk", 54, tb([17, 162, 224, 201]), 32, "secp256k1 secret key"),
    (b"p2sk", 54, tb([16, 81, 238, 189]), 32, "p256 secret key"),
    (b"edesk", 88, tb([7, 90, 60, 179, 41]), 56, "ed25519 encrypted seed"),
    (b"spesk", 88, tb([9, 237, 241, 174, 150]), 56, "secp256k1 encrypted secret key"),
    (b"p2esk", 88, tb([9, 48, 57, 115, 171]), 56, "p256_encrypted_secret_key"),
    (b"sppk", 55, tb([3, 254, 226, 86]), 33, "secp256k1 public key"),
    (b"p2pk", 55, tb([3, 178, 139, 127]), 33, "p256 public key"),
    (b"SSp", 53, tb([38, 248, 136]), 33, "secp256k1 scalar"),
    (b"GSp", 53, tb([5, 92, 0]), 33, "secp256k1 element"),
    (b"edsk", 98, tb([43, 246, 78, 7]), 64, "ed25519 secret key"),
    (b"edsig", 99, tb([9, 245, 205, 134, 18]), 64, "ed25519 signature"),
    (b"spsig", 99, tb([13, 115, 101, 19, 63]), 64, "secp256k1 signature"),
    (b"p2sig", 98, tb([54, 240, 44, 52]), 64, "p256 signature"),
    (b"sig", 96, tb([4, 130, 43]), 64, "generic signature"),
    (b'Net', 15, tb([87, 82, 0]), 4, "chain id"),
    (b'nce', 53, tb([69, 220, 169]), 32, 'seed nonce hash'),
    (b'btz1', 37, tb([1, 2, 49, 223]), 20, 'blinded public key hash'),
    (b'vh', 52, tb([1, 106, 242]), 32, 'block_payload_hash'),
]


def forge_address(value: str, tz_only=False) -> bytes:
    """Encode address or key hash into bytes.

    :param value: base58 encoded address or key_hash
    :param tz_only: True indicates that it's a key_hash (will be encoded in a more compact form)
    """
    prefix_len = 4 if value.startswith('txr1') else 3
    prefix = value[:prefix_len]
    address = b58decode_check(value)[prefix_len:]

    if prefix == 'tz1':
        res = b'\x00\x00' + address
    elif prefix == 'tz2':
        res = b'\x00\x01' + address
    elif prefix == 'tz3':
        res = b'\x00\x02' + address
    elif prefix == 'tz4':
        res = b'\x00\x03' + address
    elif prefix == 'KT1':
        res = b'\x01' + address + b'\x00'
    elif prefix == 'txr1':
        res = b'\x02' + address + b'\x00'
    elif prefix == 'sr1':
        res = b'\x03' + address + b'\x00'
    else:
        raise ValueError(f'Can\'t forge address: unknown prefix `{prefix}`')

    return res[1:] if tz_only else res


def unforge_address(data: bytes) -> str:
    """Decode address or key_hash from bytes.

    :param data: encoded address or key_hash
    :returns: base58 encoded address
    """
    tz_prefixes = {
        b'\x00\x00': b'tz1',
        b'\x00\x01': b'tz2',
        b'\x00\x02': b'tz3',
        b'\x00\x03': b'tz4',
    }

    for bin_prefix, tz_prefix in tz_prefixes.items():
        if data.startswith(bin_prefix):
            return base58_encode(data[2:], tz_prefix).decode()

    if data.startswith(b'\x01') and data.endswith(b'\x00'):
        return base58_encode(data[1:-1], b'KT1').decode()
    elif data.startswith(b'\x02') and data.endswith(b'\x00'):
        return base58_encode(data[1:-1], b'txr1').decode()
    elif data.startswith(b'\x03') and data.endswith(b'\x00'):
        return base58_encode(data[1:-1], b'sr1').decode()
    else:
        return base58_encode(data[1:], tz_prefixes[b'\x00' + data[:1]]).decode()


def base58_decode(v: bytes) -> bytes:
    """Decode data using Base58 with checksum + validate binary prefix against known kinds and cut in the end.

    :param v: Array of bytes (use string.encode())
    :returns: bytes
    """
    try:
        prefix_len = next(
            len(encoding[2]) for encoding in base58_encodings if len(v) == encoding[1] and v.startswith(encoding[0])
        )
    except StopIteration as e:
        raise ValueError('Invalid encoding, prefix or length mismatch.') from e

    return base58.b58decode_check(v)[prefix_len:]


def base58_encode(v: bytes, prefix: bytes) -> bytes:
    """Encode data using Base58 with checksum and add an according binary prefix in the end.

    :param v: Array of bytes
    :param prefix: Human-readable prefix (use b'') e.g. b'tz', b'KT', etc
    :returns: bytes (use string.decode())
    """
    try:
        encoding = next(encoding for encoding in base58_encodings if len(v) == encoding[3] and prefix == encoding[0])
    except StopIteration as e:
        raise ValueError('Invalid encoding, prefix or length mismatch.') from e
    return base58.b58encode_check(encoding[2] + v)


class ForgedAddressDecoder(Fixed32ByteSizeDecoder):
    is_big_endian = False

    @staticmethod
    def decoder_fn(data):
        return unforge_address(data)

    @classmethod
    @parse_type_str("forged")
    def from_type_str(cls, abi_type, registry):
        return ForgedAddressDecoder(value_bit_size=22 * 8)


_abi_type_map['forged'] = 'string'

registry.register(
    BaseEquals("forged"),
    encoding.BytesEncoder,
    ForgedAddressDecoder,
    label="forged",
)
