"""Offline label generation: a QR encoder, Code 39, and SVG renderers.

Both symbologies are implemented from scratch on the standard library, because
a CDN or a pip package would break the two hard constraints (no network, no
dependencies). Output is **SVG** rather than PNG: it scales crisply at any
label size, needs no raster encoder, and the browser renders it natively.

QR implementation notes (ISO/IEC 18004):

* byte mode, versions 1–10, EC levels L/M/Q/H;
* Reed–Solomon over GF(256) with the QR irreducible polynomial ``0x11D``;
* block interleaving follows the two-group structure of the standard (the
  single-group shortcut loses a codeword at version 8-M and above);
* all 8 data masks are tried and the lowest-penalty one wins;
* function patterns are tracked in a separate boolean grid, so the format and
  version areas are reserved exactly where the specification puts them — not
  the whole of row 8, which would overwrite data modules.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import config, errors
from .config import get_logger

_log = get_logger("labels")

# --------------------------------------------------------------------------- #
# GF(256) arithmetic for Reed–Solomon
# --------------------------------------------------------------------------- #

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf() -> None:
    """Build the exponent/log tables for GF(256) with polynomial 0x11D."""
    value = 1
    for index in range(255):
        _GF_EXP[index] = value
        _GF_LOG[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        _GF_EXP[index] = _GF_EXP[index - 255]


_init_gf()


def _gf_mul(a: int, b: int) -> int:
    """Multiply two GF(256) elements."""
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(degree: int) -> list[int]:
    """Return the Reed–Solomon generator polynomial of ``degree``."""
    poly = [1]
    for index in range(degree):
        new_poly = [0] * (len(poly) + 1)
        for position, coefficient in enumerate(poly):
            new_poly[position] ^= coefficient
            new_poly[position + 1] ^= _gf_mul(coefficient, _GF_EXP[index])
        poly = new_poly
    return poly


def rs_encode(data: Sequence[int], ec_count: int) -> list[int]:
    """Return ``ec_count`` Reed–Solomon check codewords for ``data``."""
    generator = _rs_generator_poly(ec_count)
    result = list(data) + [0] * ec_count
    for index in range(len(data)):
        coefficient = result[index]
        if coefficient != 0:
            log_coefficient = _GF_LOG[coefficient]
            for position in range(len(generator)):
                result[index + position] ^= _gf_mul(generator[position], _GF_EXP[log_coefficient])
    return result[len(data):]


# --------------------------------------------------------------------------- #
# Capacity tables (versions 1–10)
# --------------------------------------------------------------------------- #

#: ``(ec_per_block, blocks_g1, data_g1, blocks_g2, data_g2)`` per level/version.
_EC_TABLE: dict[str, dict[int, tuple[int, int, int, int, int]]] = {
    "L": {
        1: (7, 1, 19, 0, 0),
        2: (10, 1, 34, 0, 0),
        3: (15, 1, 55, 0, 0),
        4: (20, 1, 80, 0, 0),
        5: (26, 1, 108, 0, 0),
        6: (18, 2, 68, 0, 0),
        7: (20, 2, 78, 0, 0),
        8: (24, 2, 97, 0, 0),
        9: (30, 2, 116, 0, 0),
        10: (18, 2, 68, 2, 69),
    },
    "M": {
        1: (10, 1, 16, 0, 0),
        2: (16, 1, 28, 0, 0),
        3: (26, 1, 44, 0, 0),
        4: (18, 2, 32, 0, 0),
        5: (24, 2, 43, 0, 0),
        6: (16, 4, 27, 0, 0),
        7: (18, 4, 31, 0, 0),
        8: (22, 2, 38, 2, 39),
        9: (22, 3, 36, 2, 37),
        10: (26, 4, 43, 1, 44),
    },
    "Q": {
        1: (13, 1, 13, 0, 0),
        2: (22, 1, 22, 0, 0),
        3: (18, 2, 17, 0, 0),
        4: (26, 2, 24, 0, 0),
        5: (18, 2, 15, 2, 16),
        6: (24, 4, 19, 0, 0),
        7: (18, 2, 14, 4, 15),
        8: (22, 4, 18, 2, 19),
        9: (20, 4, 16, 4, 17),
        10: (24, 6, 19, 2, 20),
    },
    "H": {
        1: (17, 1, 9, 0, 0),
        2: (28, 1, 16, 0, 0),
        3: (22, 2, 13, 0, 0),
        4: (16, 4, 9, 0, 0),
        5: (22, 2, 11, 2, 12),
        6: (28, 4, 15, 0, 0),
        7: (26, 4, 13, 1, 14),
        8: (26, 4, 14, 2, 15),
        9: (24, 4, 12, 4, 13),
        10: (28, 6, 15, 2, 16),
    },
}

#: Alignment pattern centre positions by version.
_ALIGN: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}

_FORMAT_EC_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

MAX_QR_VERSION = 10
MAX_QR_BYTES = 271


def qr_size(version: int) -> int:
    """Return the module count of a QR version (21 for v1, +4 per version)."""
    return 21 + 4 * (version - 1)


def _total_data_codewords(level: str, version: int) -> int:
    """Return the data codeword count for a level/version pair."""
    _, blocks1, data1, blocks2, data2 = _EC_TABLE[level][version]
    return blocks1 * data1 + blocks2 * data2


def _byte_capacity(level: str, version: int) -> int:
    """Return how many payload bytes fit in byte mode at this level/version."""
    data_bits = _total_data_codewords(level, version) * 8
    count_bits = 8 if version <= 9 else 16
    overhead = 4 + count_bits
    return max(0, (data_bits - overhead) // 8)


def _choose_version(level: str, byte_length: int) -> int:
    """Return the smallest version that fits, or raise ``qr_data_too_long``."""
    for version in range(1, MAX_QR_VERSION + 1):
        if byte_length <= _byte_capacity(level, version):
            return version
    raise errors.ImriError(
        errors.QR_DATA_TOO_LONG,
        errors.status_for(errors.QR_DATA_TOO_LONG),
        details={"bytes": byte_length, "max": _byte_capacity(level, MAX_QR_VERSION)},
    )


# --------------------------------------------------------------------------- #
# Bit-level encoding
# --------------------------------------------------------------------------- #


def _encode_data(data: bytes, version: int, level: str) -> list[int]:
    """Encode ``data`` in byte mode and pad to the full data capacity."""
    capacity = _total_data_codewords(level, version)
    count_bits = 8 if version <= 9 else 16

    bits: list[int] = [0, 1, 0, 0]  # byte mode indicator
    length = len(data)
    for shift in range(count_bits - 1, -1, -1):
        bits.append((length >> shift) & 1)
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)

    capacity_bits = capacity * 8
    for _ in range(min(4, capacity_bits - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)

    pad = (0xEC, 0x11)
    index = 0
    while len(bits) < capacity_bits:
        value = pad[index % 2]
        index += 1
        for shift in range(7, -1, -1):
            bits.append((value >> shift) & 1)

    codewords: list[int] = []
    for start in range(0, len(bits), 8):
        byte = 0
        for offset in range(8):
            byte = (byte << 1) | bits[start + offset]
        codewords.append(byte)
    return codewords


def _interleave(codewords: Sequence[int], version: int, level: str) -> list[int]:
    """Split into blocks, add Reed–Solomon, and interleave per the standard."""
    ec_per_block, blocks1, data1, blocks2, data2 = _EC_TABLE[level][version]
    blocks: list[list[int]] = []
    position = 0
    for _ in range(blocks1):
        blocks.append(list(codewords[position : position + data1]))
        position += data1
    for _ in range(blocks2):
        blocks.append(list(codewords[position : position + data2]))
        position += data2

    ec_blocks = [rs_encode(block, ec_per_block) for block in blocks]

    result: list[int] = []
    longest = max(len(block) for block in blocks)
    for index in range(longest):
        for block in blocks:
            if index < len(block):
                result.append(block[index])
    for index in range(ec_per_block):
        for block in ec_blocks:
            result.append(block[index])
    return result


# --------------------------------------------------------------------------- #
# Matrix construction
# --------------------------------------------------------------------------- #


def _mask_applies(mask_id: int, row: int, col: int) -> bool:
    """Return True when mask ``mask_id`` inverts ``(row, col)``."""
    if mask_id == 0:
        return (row + col) % 2 == 0
    if mask_id == 1:
        return row % 2 == 0
    if mask_id == 2:
        return col % 3 == 0
    if mask_id == 3:
        return (row + col) % 3 == 0
    if mask_id == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask_id == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if mask_id == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row + col) % 2 + (row * col) % 3) % 2 == 0


def _format_bits(level: str, mask_id: int) -> int:
    """Return the 15-bit format information word (BCH + XOR mask)."""
    data = (_FORMAT_EC_BITS[level] << 3) | mask_id
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return ((data << 10) | remainder) ^ 0x5412


def _version_bits(version: int) -> int:
    """Return the 18-bit version information word (BCH)."""
    remainder = version
    for _ in range(12):
        remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
    return (version << 12) | remainder


class _MatrixBuilder:
    """Builds the module grid for one version/level/mask combination."""

    def __init__(self, version: int) -> None:
        self.version = version
        self.size = qr_size(version)
        self.modules: list[list[int]] = [[0] * self.size for _ in range(self.size)]
        self.function: list[list[bool]] = [[False] * self.size for _ in range(self.size)]

    def set_function(self, row: int, col: int, dark: bool) -> None:
        """Write a module that belongs to a function pattern."""
        if 0 <= row < self.size and 0 <= col < self.size:
            self.modules[row][col] = 1 if dark else 0
            self.function[row][col] = True

    def draw_finder(self, row: int, col: int) -> None:
        """Draw a 7x7 finder plus its separator (9x9 centred on the finder)."""
        for delta_row in range(-4, 5):
            for delta_col in range(-4, 5):
                distance = max(abs(delta_row), abs(delta_col))
                self.set_function(
                    row + delta_row,
                    col + delta_col,
                    distance != 2 and distance != 4,
                )

    def draw_alignment(self, row: int, col: int) -> None:
        """Draw a 5x5 alignment pattern centred on ``(row, col)``."""
        for delta_row in range(-2, 3):
            for delta_col in range(-2, 3):
                self.set_function(
                    row + delta_row,
                    col + delta_col,
                    max(abs(delta_row), abs(delta_col)) != 1,
                )

    def draw_function_patterns(self, level: str) -> None:
        """Reserve every non-data module: timing, finders, alignment, format."""
        for index in range(self.size):
            self.set_function(6, index, index % 2 == 0)
            self.set_function(index, 6, index % 2 == 0)

        self.draw_finder(3, 3)
        self.draw_finder(3, self.size - 4)
        self.draw_finder(self.size - 4, 3)

        centres = _ALIGN[self.version]
        for row_index, row in enumerate(centres):
            for col_index, col in enumerate(centres):
                if (
                    (row_index == 0 and col_index == 0)
                    or (row_index == 0 and col_index == len(centres) - 1)
                    or (row_index == len(centres) - 1 and col_index == 0)
                ):
                    continue
                self.draw_alignment(row, col)

        self.draw_format(level, 0)
        self.draw_version()

    def draw_format(self, level: str, mask_id: int) -> None:
        """Place both copies of the 15-bit format information."""
        bits = _format_bits(level, mask_id)

        def bit(index: int) -> bool:
            return bool((bits >> index) & 1)

        for index in range(6):
            self.set_function(8, index, bit(index))
        self.set_function(8, 7, bit(6))
        self.set_function(8, 8, bit(7))
        self.set_function(7, 8, bit(8))
        for index in range(9, 15):
            self.set_function(14 - index, 8, bit(index))

        for index in range(8):
            self.set_function(self.size - 1 - index, 8, bit(index))
        for index in range(8, 15):
            self.set_function(8, self.size - 15 + index, bit(index))
        self.set_function(self.size - 8, 8, True)  # always-dark module

    def draw_version(self) -> None:
        """Place both copies of the 18-bit version information (v7+)."""
        if self.version < 7:
            return
        bits = _version_bits(self.version)
        for index in range(18):
            dark = bool((bits >> index) & 1)
            a = self.size - 11 + index % 3
            b = index // 3
            self.set_function(b, a, dark)
            self.set_function(a, b, dark)

    def place_codewords(self, codewords: Sequence[int]) -> None:
        """Fill the remaining modules in the standard zig-zag order."""
        bits: list[int] = []
        for codeword in codewords:
            for shift in range(7, -1, -1):
                bits.append((codeword >> shift) & 1)
        total = len(bits)
        index = 0

        col = self.size - 1
        upward = True
        while col > 0:
            if col == 6:  # skip the vertical timing column
                col -= 1
            rows = range(self.size - 1, -1, -1) if upward else range(self.size)
            for row in rows:
                for candidate in (col, col - 1):
                    if candidate < 0 or self.function[row][candidate]:
                        continue
                    self.modules[row][candidate] = bits[index] if index < total else 0
                    index += 1
            upward = not upward
            col -= 2

    def apply_mask(self, mask_id: int) -> None:
        """XOR the data mask over every non-function module."""
        for row in range(self.size):
            for col in range(self.size):
                if self.function[row][col]:
                    continue
                if _mask_applies(mask_id, row, col):
                    self.modules[row][col] ^= 1

    def penalty(self) -> int:
        """Score the grid with the four standard penalty rules."""
        size = self.size
        score = 0

        # Rule 1 — runs of five or more identical modules in a row or column.
        for row in range(size):
            run_colour = self.modules[row][0]
            run_length = 1
            for col in range(1, size):
                if self.modules[row][col] == run_colour:
                    run_length += 1
                    if run_length == 5:
                        score += 3
                    elif run_length > 5:
                        score += 1
                else:
                    run_colour = self.modules[row][col]
                    run_length = 1
        for col in range(size):
            run_colour = self.modules[0][col]
            run_length = 1
            for row in range(1, size):
                if self.modules[row][col] == run_colour:
                    run_length += 1
                    if run_length == 5:
                        score += 3
                    elif run_length > 5:
                        score += 1
                else:
                    run_colour = self.modules[row][col]
                    run_length = 1

        # Rule 2 — 2x2 blocks of the same colour.
        for row in range(size - 1):
            for col in range(size - 1):
                value = self.modules[row][col]
                if (
                    value == self.modules[row][col + 1]
                    and value == self.modules[row + 1][col]
                    and value == self.modules[row + 1][col + 1]
                ):
                    score += 3

        # Rule 3 — finder-like 1:1:3:1:1 patterns.
        pattern_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
        pattern_b = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
        for row in range(size):
            for col in range(size - 10):
                window = self.modules[row][col : col + 11]
                if window == pattern_a or window == pattern_b:
                    score += 40
        for col in range(size):
            for row in range(size - 10):
                window = [self.modules[row + offset][col] for offset in range(11)]
                if window == pattern_a or window == pattern_b:
                    score += 40

        # Rule 4 — deviation from a 50 % dark ratio.
        dark = sum(sum(row) for row in self.modules)
        total = size * size
        ratio = dark * 100 // total
        score += (abs(ratio - 50) // 5) * 10
        return score


def qr_matrix(data: Any, ec: str = "M", version: int | None = None) -> list[list[int]]:
    """Return the QR module grid (0/1) for ``data``."""
    level = (ec or "M").strip().upper()
    if level not in _EC_TABLE:
        level = "M"
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if not payload:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )

    chosen = int(version) if version else _choose_version(level, len(payload))
    if chosen < 1 or chosen > MAX_QR_VERSION:
        raise errors.ImriError(
            errors.QR_DATA_TOO_LONG, errors.status_for(errors.QR_DATA_TOO_LONG)
        )
    if len(payload) > _byte_capacity(level, chosen):
        raise errors.ImriError(
            errors.QR_DATA_TOO_LONG,
            errors.status_for(errors.QR_DATA_TOO_LONG),
            details={"bytes": len(payload), "max": _byte_capacity(level, chosen)},
        )

    codewords = _encode_data(payload, chosen, level)
    interleaved = _interleave(codewords, chosen, level)

    best_matrix: list[list[int]] | None = None
    best_score: int | None = None
    for mask_id in range(8):
        builder = _MatrixBuilder(chosen)
        builder.draw_function_patterns(level)
        builder.place_codewords(interleaved)
        builder.apply_mask(mask_id)
        builder.draw_format(level, mask_id)
        score = builder.penalty()
        if best_score is None or score < best_score:
            best_score = score
            best_matrix = builder.modules
    return best_matrix or [[0]]


# --------------------------------------------------------------------------- #
# QR -> SVG
# --------------------------------------------------------------------------- #


def qr_svg(data: Any, scale: int = 8, border: int = 4, ec: str = "M") -> str:
    """Render ``data`` as a QR code SVG string."""
    matrix = qr_matrix(data, ec=ec)
    module_count = len(matrix)
    quiet = max(0, int(border))
    unit = max(1, int(scale))
    dimension = (module_count + quiet * 2) * unit

    commands: list[str] = []
    for row_index, row in enumerate(matrix):
        col_index = 0
        while col_index < module_count:
            if not row[col_index]:
                col_index += 1
                continue
            run_start = col_index
            while col_index < module_count and row[col_index]:
                col_index += 1
            run_length = col_index - run_start
            x = (run_start + quiet) * unit
            y = (row_index + quiet) * unit
            commands.append("M%d %dh%dv%dh-%dz" % (x, y, run_length * unit, unit, run_length * unit))

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" shape-rendering="crispEdges" role="img">'
        '<rect width="%d" height="%d" fill="#FFFFFF"/>'
        '<path d="%s" fill="#000000"/>'
        "</svg>"
    ) % (
        dimension,
        dimension,
        dimension,
        dimension,
        dimension,
        dimension,
        "".join(commands),
    )


# --------------------------------------------------------------------------- #
# Code 39
# --------------------------------------------------------------------------- #

#: Nine-element wide/narrow mask per character; even indexes are bars.
_CODE39: dict[str, int] = {
    "0": 0b000110100,
    "1": 0b100100001,
    "2": 0b001100001,
    "3": 0b101100000,
    "4": 0b000110001,
    "5": 0b100110000,
    "6": 0b001110000,
    "7": 0b000100101,
    "8": 0b100100100,
    "9": 0b001100100,
    "A": 0b100001001,
    "B": 0b001001001,
    "C": 0b101001000,
    "D": 0b000011001,
    "E": 0b100011000,
    "F": 0b001011000,
    "G": 0b000001101,
    "H": 0b100001100,
    "I": 0b001001100,
    "J": 0b000011100,
    "K": 0b100000011,
    "L": 0b001000011,
    "M": 0b101000010,
    "N": 0b000010011,
    "O": 0b100010010,
    "P": 0b001010010,
    "Q": 0b000000111,
    "R": 0b100000110,
    "S": 0b001000110,
    "T": 0b000010110,
    "U": 0b110000001,
    "V": 0b011000001,
    "W": 0b111000000,
    "X": 0b010010001,
    "Y": 0b110010000,
    "Z": 0b011010000,
    "-": 0b010000101,
    ".": 0b110000100,
    " ": 0b011000100,
    "$": 0b010101000,
    "/": 0b010100010,
    "+": 0b010001010,
    "%": 0b000101010,
    "*": 0b010010100,
}

_CODE39_START_STOP = "*"


def code39_chars_ok(text: Any) -> bool:
    """Return True when every character is representable in Code 39."""
    return all(char.upper() in _CODE39 for char in str(text or ""))


def code39_modules(text: Any) -> str:
    """Return the module string (``1`` dark, ``0`` light) for ``text``."""
    payload = str(text or "").upper()
    for char in payload:
        if char not in _CODE39:
            raise errors.ImriError(
                errors.CODE39_UNSUPPORTED_CHAR,
                errors.status_for(errors.CODE39_UNSUPPORTED_CHAR),
                details={"character": char},
            )
    if not payload:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )

    out: list[str] = []
    for char in _CODE39_START_STOP + payload + _CODE39_START_STOP:
        mask = _CODE39[char]
        for position in range(9):
            wide = bool((mask >> (8 - position)) & 1)
            dark = position % 2 == 0
            width = 3 if wide else 1
            out.append(("1" if dark else "0") * width)
        out.append("0")  # narrow inter-character gap
    return "".join(out)


def code39_pattern(text: Any) -> str:
    """Return the module string for ``text`` (start/stop guards included)."""
    return code39_modules(text)


def code39_svg(
    data: Any, scale: int = 2, height: int = 60, caption: bool = True
) -> str:
    """Render ``data`` as a Code 39 barcode SVG with an optional caption."""
    modules = code39_modules(data)
    unit = max(1, int(scale))
    bar_height = max(10, int(height))
    width = len(modules) * unit
    caption_height = 16 if caption else 0
    total_height = bar_height + caption_height

    commands: list[str] = []
    index = 0
    while index < len(modules):
        if modules[index] == "0":
            index += 1
            continue
        start = index
        while index < len(modules) and modules[index] == "1":
            index += 1
        run = index - start
        commands.append("M%d 0h%dv%dh-%dz" % (start * unit, run * unit, bar_height, run * unit))

    label = ""
    if caption:
        label = (
            '<text x="%d" y="%d" font-family="monospace" font-size="12" '
            'text-anchor="middle" fill="#000000">%s</text>'
            % (width // 2, bar_height + 13, _xml_escape(str(data)))
        )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img">'
        '<rect width="%d" height="%d" fill="#FFFFFF"/>'
        '<path d="%s" fill="#000000"/>%s</svg>'
    ) % (
        width,
        total_height,
        width,
        total_height,
        width,
        total_height,
        "".join(commands),
        label,
    )


def _xml_escape(text: Any) -> str:
    """Escape text for XML content."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# --------------------------------------------------------------------------- #
# Label composition
# --------------------------------------------------------------------------- #


def book_label_svg(
    copy: Mapping[str, Any],
    book: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
) -> str:
    """Compose a spine/cover label: title, accession, QR and Code 39."""
    opts = dict(options or {})
    width = int(opts.get("width", 320))
    height = int(opts.get("height", 180))
    scale = int(opts.get("scale", 4))
    with_code39 = config.to_bool(opts.get("code39", True))

    barcode = str(copy.get("barcode") or book.get("barcode") or book.get("accession_no") or "")
    title = str(book.get("title") or "")
    accession = str(book.get("accession_no") or "")
    copy_no = copy.get("copy_no")
    call_no = str(book.get("call_no") or book.get("dewey") or "")

    if not barcode:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )

    qr = qr_svg(barcode, scale=scale, border=2, ec="M")
    qr_size_px = (len(qr_matrix(barcode, ec="M")) + 4) * max(1, scale)

    parts: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img">' % (width, height, width, height),
        '<rect width="%d" height="%d" rx="6" fill="#FFFFFF" stroke="#D0D5DD"/>' % (width, height),
        '<text x="12" y="26" font-family="Segoe UI, sans-serif" font-size="13" '
        'font-weight="600" fill="#101828">%s</text>' % _xml_escape(_clip(title, 34)),
    ]
    if call_no:
        parts.append(
            '<text x="12" y="44" font-family="Segoe UI, sans-serif" font-size="11" '
            'fill="#475467">%s</text>' % _xml_escape(_clip(call_no, 40))
        )
    parts.append(
        '<text x="12" y="%d" font-family="Consolas, monospace" font-size="12" '
        'fill="#101828">%s</text>'
        % (height - 26, _xml_escape(accession))
    )
    if copy_no:
        parts.append(
            '<text x="12" y="%d" font-family="Segoe UI, sans-serif" font-size="11" '
            'fill="#475467">copy %s</text>' % (height - 10, _xml_escape(copy_no))
        )

    qr_x = width - qr_size_px - 12
    qr_y = 12
    parts.append('<g transform="translate(%d,%d)">%s</g>' % (qr_x, qr_y, qr))

    if with_code39 and code39_chars_ok(barcode):
        barcode_svg = code39_svg(barcode, scale=1, height=34, caption=True)
        bar_width = len(code39_modules(barcode))
        parts.append(
            '<g transform="translate(12,%d) scale(%.3f,1)">%s</g>'
            % (height - 62, min(1.0, (width - 24) / max(1, bar_width)), barcode_svg)
        )

    parts.append("</svg>")
    return "".join(parts)


def member_card_svg(
    member: Mapping[str, Any], options: Mapping[str, Any] | None = None
) -> str:
    """Compose a member card: name, code, QR and Code 39."""
    opts = dict(options or {})
    width = int(opts.get("width", 340))
    height = int(opts.get("height", 214))
    scale = int(opts.get("scale", 3))

    code = str(member.get("member_code") or "")
    if not code:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )
    name = str(member.get("name") or "")
    detail_bits = [
        str(member.get("class_name") or ""),
        str(member.get("form") or ""),
        str(member.get("member_type") or ""),
    ]
    detail = " · ".join(bit for bit in detail_bits if bit)
    school = str(opts.get("school_name") or "")

    qr = qr_svg(code, scale=scale, border=2, ec="M")
    qr_size_px = (len(qr_matrix(code, ec="M")) + 4) * max(1, scale)

    parts: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img">' % (width, height, width, height),
        '<rect width="%d" height="%d" rx="10" fill="#FFFFFF" stroke="#D0D5DD"/>' % (width, height),
        '<rect width="%d" height="6" rx="3" fill="#4F46E5"/>' % width,
    ]
    if school:
        parts.append(
            '<text x="14" y="26" font-family="Segoe UI, sans-serif" font-size="10" '
            'fill="#667085">%s</text>' % _xml_escape(_clip(school, 44))
        )
    parts.append(
        '<text x="14" y="50" font-family="Segoe UI, sans-serif" font-size="15" '
        'font-weight="600" fill="#101828">%s</text>' % _xml_escape(_clip(name, 30))
    )
    if detail:
        parts.append(
            '<text x="14" y="68" font-family="Segoe UI, sans-serif" font-size="11" '
            'fill="#475467">%s</text>' % _xml_escape(_clip(detail, 44))
        )
    parts.append(
        '<text x="14" y="%d" font-family="Consolas, monospace" font-size="16" '
        'font-weight="600" fill="#101828">%s</text>' % (height - 46, _xml_escape(code))
    )

    parts.append(
        '<g transform="translate(%d,14)">%s</g>' % (width - qr_size_px - 14, qr)
    )

    if code39_chars_ok(code):
        barcode_svg = code39_svg(code, scale=1, height=30, caption=False)
        bar_width = len(code39_modules(code))
        parts.append(
            '<g transform="translate(14,%d) scale(%.3f,1)">%s</g>'
            % (height - 34, min(1.0, (width - 28) / max(1, bar_width)), barcode_svg)
        )

    parts.append("</svg>")
    return "".join(parts)


def _clip(text: Any, limit: int) -> str:
    """Clamp a label string to ``limit`` characters with an ellipsis."""
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 1] + "\u2026"


#: ``"3x8"`` -> (columns, rows).
_LAYOUTS: dict[str, tuple[int, int]] = {
    "3x8": (3, 8),
    "2x5": (2, 5),
    "4x10": (4, 10),
    "1x1": (1, 1),
}


def sheet_svg(
    items: Iterable[Mapping[str, Any]],
    layout: str = "3x8",
    paper: str = "A4",
    options: Mapping[str, Any] | None = None,
) -> str:
    """Compose a printable sticker sheet as an HTML document.

    Returns an HTML string (rather than a raw ``<svg>``) because a sheet is
    paginated across A4 pages, and page breaks are a CSS concept. The name is
    kept for parity with the reference implementation.
    """
    opts = dict(options or {})
    columns, rows = _LAYOUTS.get(str(layout or "3x8"), _LAYOUTS["3x8"])
    per_page = max(1, columns * rows)
    labels = [book_label_svg(item.get("copy") or {}, item.get("book") or {}, opts) for item in items]
    if not labels:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )

    pages: list[list[str]] = []
    for start in range(0, len(labels), per_page):
        pages.append(labels[start : start + per_page])

    cut_guides = config.to_bool(opts.get("cut_guides", True))
    cell_border = "1px dashed #CBD5E1" if cut_guides else "none"
    paper_name = str(paper or "A4")
    css_size = "210mm 297mm" if paper_name.upper() == "A4" else "216mm 279mm"

    body: list[str] = []
    for page_index, page in enumerate(pages):
        cells = "".join('<div class="cell">%s</div>' % label for label in page)
        body.append(
            '<section class="sheet" style="page-break-after:%s">%s</section>'
            % ("always" if page_index < len(pages) - 1 else "auto", cells)
        )

    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>IMRI labels</title>"
        "<style>"
        "@page { size: %s; margin: 8mm; }"
        "body { margin:0; font-family:'Segoe UI',sans-serif; background:#FFFFFF; }"
        ".sheet { display:grid; grid-template-columns:repeat(%d, 1fr); gap:2mm; "
        "width:%s; min-height:%s; }"
        ".cell { border:%s; padding:1mm; display:flex; align-items:center; "
        "justify-content:center; break-inside:avoid; }"
        ".cell svg { width:100%%; height:auto; }"
        "@media print { body { background:#FFFFFF; } }"
        "</style></head><body>%s</body></html>"
    ) % (
        css_size,
        columns,
        css_size.split(" ")[0],
        css_size.split(" ")[1],
        cell_border,
        "".join(body),
    )


class LabelService:
    """Thin façade so the API layer has one object to talk to."""

    def qr_matrix(self, data: Any, ec: str = "M") -> list[list[int]]:
        """Return the QR module grid."""
        return qr_matrix(data, ec=ec)

    def qr_svg(self, data: Any, scale: int = 8, border: int = 4, ec: str = "M") -> str:
        """Return a QR code SVG."""
        return qr_svg(data, scale=scale, border=border, ec=ec)

    def code39_pattern(self, text: Any) -> str:
        """Return the Code 39 module string."""
        return code39_pattern(text)

    def code39_svg(
        self, data: Any, scale: int = 2, height: int = 60, caption: bool = True
    ) -> str:
        """Return a Code 39 SVG."""
        return code39_svg(data, scale=scale, height=height, caption=caption)

    def book_label_svg(
        self, copy: Mapping[str, Any], book: Mapping[str, Any], options: Mapping[str, Any] | None = None
    ) -> str:
        """Return a full book label SVG."""
        return book_label_svg(copy, book, options)

    def member_card_svg(
        self, member: Mapping[str, Any], options: Mapping[str, Any] | None = None
    ) -> str:
        """Return a member card SVG."""
        return member_card_svg(member, options)

    def sheet_svg(
        self,
        items: Iterable[Mapping[str, Any]],
        layout: str = "3x8",
        paper: str = "A4",
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Return a printable label sheet as HTML."""
        return sheet_svg(items, layout=layout, paper=paper, options=options)
