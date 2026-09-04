"""Pure-Python Chromaprint fingerprint decompression and comparison.

Ported directly from the official chromaprint C++ source (MIT licensed,
https://github.com/acoustid/chromaprint): src/fingerprint_decompressor.cpp,
src/utils/{,un}pack_int{3,5}_array.h, src/utils/base64.h, and the matching
algorithm from pyacoustid's acoustid.py (_match_fingerprints/compare_fingerprints).

Exists because acoustid.compare_fingerprints() needs the native
libchromaprint.dll/.so, which is not available on this machine (only the
standalone fpcalc.exe binary is) - this reimplements just enough of the
library in Python to decompress and compare the fingerprint strings fpcalc
already produces, with no native dependency at all.

Validated against chromaprint's own test fixture (tests/data/test.mp3 +
tests/data/test.mp3.fpcalc.out from the chromaprint repo): running fpcalc on
that file and decoding the result with decode_fingerprint() reproduces the
repo's recorded raw FINGERPRINT= values exactly, element for element.
"""
import base64

_MAX_NORMAL_VALUE = 7  # kMaxNormalValue = (1 << kNormalBits) - 1, kNormalBits = 3
_MAX_ALIGN_OFFSET = 120
_MAX_BIT_ERROR = 2


def _unpack_int3_array(data):
    out = []
    n = len(data)
    i = 0
    while n - i >= 3:
        s0, s1, s2 = data[i], data[i + 1], data[i + 2]
        out.append(s0 & 0x07)
        out.append((s0 & 0x38) >> 3)
        out.append(((s0 & 0xc0) >> 6) | ((s1 & 0x01) << 2))
        out.append((s1 & 0x0e) >> 1)
        out.append((s1 & 0x70) >> 4)
        out.append(((s1 & 0x80) >> 7) | ((s2 & 0x03) << 1))
        out.append((s2 & 0x1c) >> 2)
        out.append((s2 & 0xe0) >> 5)
        i += 3
    rem = n - i
    if rem == 2:
        s0, s1 = data[i], data[i + 1]
        out.append(s0 & 0x07)
        out.append((s0 & 0x38) >> 3)
        out.append(((s0 & 0xc0) >> 6) | ((s1 & 0x01) << 2))
        out.append((s1 & 0x0e) >> 1)
        out.append((s1 & 0x70) >> 4)
    elif rem == 1:
        s0 = data[i]
        out.append(s0 & 0x07)
        out.append((s0 & 0x38) >> 3)
    return out


def _unpack_int5_array(data):
    out = []
    n = len(data)
    i = 0
    while n - i >= 5:
        s0, s1, s2, s3, s4 = data[i], data[i + 1], data[i + 2], data[i + 3], data[i + 4]
        out.append(s0 & 0x1f)
        out.append(((s0 & 0xe0) >> 5) | ((s1 & 0x03) << 3))
        out.append((s1 & 0x7c) >> 2)
        out.append(((s1 & 0x80) >> 7) | ((s2 & 0x0f) << 1))
        out.append(((s2 & 0xf0) >> 4) | ((s3 & 0x01) << 4))
        out.append((s3 & 0x3e) >> 1)
        out.append(((s3 & 0xc0) >> 6) | ((s4 & 0x07) << 2))
        out.append((s4 & 0xf8) >> 3)
        i += 5
    rem = n - i
    if rem == 4:
        s0, s1, s2, s3 = data[i], data[i + 1], data[i + 2], data[i + 3]
        out.append(s0 & 0x1f)
        out.append(((s0 & 0xe0) >> 5) | ((s1 & 0x03) << 3))
        out.append((s1 & 0x7c) >> 2)
        out.append(((s1 & 0x80) >> 7) | ((s2 & 0x0f) << 1))
        out.append(((s2 & 0xf0) >> 4) | ((s3 & 0x01) << 4))
        out.append((s3 & 0x3e) >> 1)
    elif rem == 3:
        s0, s1, s2 = data[i], data[i + 1], data[i + 2]
        out.append(s0 & 0x1f)
        out.append(((s0 & 0xe0) >> 5) | ((s1 & 0x03) << 3))
        out.append((s1 & 0x7c) >> 2)
        out.append(((s1 & 0x80) >> 7) | ((s2 & 0x0f) << 1))
    elif rem == 2:
        s0, s1 = data[i], data[i + 1]
        out.append(s0 & 0x1f)
        out.append(((s0 & 0xe0) >> 5) | ((s1 & 0x03) << 3))
        out.append((s1 & 0x7c) >> 2)
    elif rem == 1:
        s0 = data[i]
        out.append(s0 & 0x1f)
    return out


def _get_packed_int3_size(size):
    return (size * 3 + 7) // 8


def _get_packed_int5_size(size):
    return (size * 5 + 7) // 8


def _base64_decode(s):
    # Chromaprint's alphabet (A-Z a-z 0-9 - _) is identical to Python's
    # "urlsafe" base64 alphabet, just emitted without '=' padding.
    padding = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def decode_fingerprint(fingerprint):
    """Decompress a base64 Chromaprint fingerprint (fpcalc's raw -json
    'fingerprint' field) into (list_of_uint32_subfingerprints, algorithm_id).
    Faithful port of FingerprintDecompressor::Decompress."""
    raw = _base64_decode(fingerprint)
    if len(raw) < 4:
        raise ValueError("Invalid fingerprint: shorter than 4 bytes")

    algorithm = raw[0]
    size = (raw[1] << 16) | (raw[2] << 8) | raw[3]

    offset = 4
    bits = _unpack_int3_array(raw[offset:])

    found_values = 0
    num_exceptional_bits = 0
    truncate_at = None
    for i, b in enumerate(bits):
        if b == 0:
            found_values += 1
            if found_values == size:
                truncate_at = i + 1
                break
        elif b == _MAX_NORMAL_VALUE:
            num_exceptional_bits += 1

    if found_values != size:
        raise ValueError("Invalid fingerprint: not enough data for normal bits")
    bits = bits[:truncate_at]

    offset += _get_packed_int3_size(len(bits))

    if num_exceptional_bits:
        exceptional_len = _get_packed_int5_size(num_exceptional_bits)
        if len(raw) < offset + exceptional_len:
            raise ValueError("Invalid fingerprint: not enough data for exceptional bits")
        exceptional_bits = _unpack_int5_array(raw[offset:offset + exceptional_len])
        j = 0
        for i, b in enumerate(bits):
            if b == _MAX_NORMAL_VALUE:
                bits[i] = b + exceptional_bits[j]
                j += 1

    output = [0] * size
    i = 0
    last_bit = 0
    value = 0
    for b in bits:
        if b == 0:
            output[i] = value
            last_bit = 0
            i += 1
        else:
            last_bit += b
            value ^= (1 << (last_bit - 1))

    return output, algorithm


def _popcount(x):
    return bin(x).count("1")


def compare_fingerprints(fp1, fp2):
    """Similarity score in [0, 1] between two base64 Chromaprint fingerprint
    strings, matching acoustid.compare_fingerprints()'s algorithm exactly
    (same MAX_ALIGN_OFFSET/MAX_BIT_ERROR constants and sliding-offset
    bit-error-count matcher) without needing libchromaprint."""
    a, _ = decode_fingerprint(fp1)
    b, _ = decode_fingerprint(fp2)

    asize, bsize = len(a), len(b)
    if asize == 0 or bsize == 0:
        return 0.0

    counts = [0] * (asize + bsize + 1)
    for i in range(asize):
        jbegin = max(0, i - _MAX_ALIGN_OFFSET)
        jend = min(bsize, i + _MAX_ALIGN_OFFSET)
        ai = a[i]
        for j in range(jbegin, jend):
            biterror = _popcount(ai ^ b[j])
            if biterror <= _MAX_BIT_ERROR:
                counts[i - j + bsize] += 1
    topcount = max(counts)
    return topcount / min(asize, bsize)
