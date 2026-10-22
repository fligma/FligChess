"""QR codes without a dependency.

The app has to run on a plain install with nothing but pygame in it, so the
picture a guest scans is drawn here rather than by a package. Byte mode is
all a URL ever needs, and error level L across versions 1-9 covers every
address a LAN will hand out.

`encode()` returns rows of 0/1, dark module first, with the quiet zone left
off: whoever draws the code decides how wide a margin to put around it.
"""

# version -> (data codewords, error codewords per block, blocks), level L
VERSIONS = {
    1: (19, 7, 1), 2: (34, 10, 1), 3: (55, 15, 1), 4: (80, 20, 1),
    5: (108, 26, 1), 6: (136, 18, 2), 7: (156, 20, 2), 8: (194, 24, 2),
    9: (232, 30, 2),
}

# the centres of the alignment patterns each version carries
ALIGN = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34),
         7: (6, 22, 38), 8: (6, 24, 42), 9: (6, 26, 46)}

LEVEL_L_BITS = 0b01        # the two format bits that say "error level L"

# GF(256) tables, primitive polynomial x^8 + x^4 + x^3 + x^2 + 1
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if not a or not b:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    """The Reed-Solomon generator polynomial, highest power first, monic."""
    poly = [1]
    for i in range(degree):
        out = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            out[j] ^= coef
            out[j + 1] ^= _mul(coef, _EXP[i])
        poly = out
    return poly


def _ecc(data, degree):
    """The check bytes for `data`: the remainder of data * x^degree / g(x)."""
    gen = _generator(degree)
    res = [0] * degree
    for byte in data:
        factor = byte ^ res[0]
        res = res[1:] + [0]
        for i, coef in enumerate(gen[1:]):
            res[i] ^= _mul(coef, factor)
    return res


def _pick(text):
    """The smallest version byte mode fits `text` in, or None past 9."""
    need = 12 + len(text.encode("utf-8")) * 8     # mode + length + payload
    for version in sorted(VERSIONS):
        if need <= VERSIONS[version][0] * 8:
            return version
    return None


def _codewords(text, version):
    """The data bytes: mode, length, payload, terminator and pad bytes."""
    data = text.encode("utf-8")
    bits = [0, 1, 0, 0]                           # byte mode
    for i in range(7, -1, -1):                    # 8-bit length, versions 1-9
        bits.append((len(data) >> i) & 1)
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    room = VERSIONS[version][0] * 8
    bits.extend([0] * min(4, room - len(bits)))   # terminator, if room is left
    while len(bits) % 8:
        bits.append(0)
    out = []
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit
        out.append(byte)
    filled = len(out)
    pads = (0xEC, 0x11)
    while len(out) < VERSIONS[version][0]:       # alternating pad bytes
        out.append(pads[(len(out) - filled) % 2])
    return out


def _interleave(version, data):
    """Every block's data and check bytes, woven in the order the modules are
    filled: the first byte of each block, then the second, then the checks."""
    total, per_block, blocks = VERSIONS[version]
    size = total // blocks
    parts = [data[i * size:(i + 1) * size] for i in range(blocks)]
    checks = [_ecc(part, per_block) for part in parts]
    out = []
    for i in range(size):
        for part in parts:
            out.append(part[i])
    for i in range(per_block):
        for check in checks:
            out.append(check[i])
    return out


def _bits(values):
    out = []
    for value in values:
        for i in range(7, -1, -1):
            out.append((value >> i) & 1)
    return out


class _Grid:
    """The modules of one symbol, and which of them a pattern owns."""

    def __init__(self, version):
        self.version = version
        self.size = 4 * version + 17
        self.dark = [[0] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set(self, row, col, value, fixed=True):
        if 0 <= row < self.size and 0 <= col < self.size:
            self.dark[row][col] = value
            if fixed:
                self.fixed[row][col] = True

    def reserve(self, row, col):
        if 0 <= row < self.size and 0 <= col < self.size:
            self.fixed[row][col] = True

    def _finder(self, row, col):
        """The 7x7 eye plus the separator that keeps it clear of the data;
        (row, col) is the eye's own top left corner."""
        for i in range(-1, 8):
            for j in range(-1, 8):
                d = max(abs(i - 3), abs(j - 3))
                self.set(row + i, col + j, 1 if d == 3 or d <= 1 else 0)

    def _alignment(self, row, col):
        for i in range(-2, 3):
            for j in range(-2, 3):
                self.set(row + i, col + j, 1 if max(abs(i), abs(j)) != 1 else 0)

    def build_patterns(self):
        size = self.size
        self._finder(0, 0)
        self._finder(0, size - 7)
        self._finder(size - 7, 0)
        for i in range(8, size - 8):       # timing lines between the eyes
            self.set(6, i, int(i % 2 == 0))
            self.set(i, 6, int(i % 2 == 0))
        for row in ALIGN[self.version]:
            for col in ALIGN[self.version]:
                eye = ((row == 6 and col == 6)
                       or (row == 6 and col == size - 7)
                       or (row == size - 7 and col == 6))
                if not eye:
                    self._alignment(row, col)
        self.set(size - 8, 8, 1)           # the dark module, always lit
        for i in range(9):                 # room for the format bits
            self.reserve(8, i)
            self.reserve(i, 8)
        for i in range(7):                 # their copy, up the left eye's side
            self.reserve(size - 1 - i, 8)
        for i in range(8):                 # and along the top eye's bottom
            self.reserve(8, size - 1 - i)
        if self.version >= 7:              # ... and for the version bits
            for i in range(6):
                for j in range(3):
                    self.reserve(size - 11 + j, i)
                    self.reserve(i, size - 11 + j)

    def place_data(self, bits):
        """The data bits laid up the two-module column pairs that run from the
        bottom right, alternating direction each pair and skipping every
        module a pattern owns. The vertical timing line at column 6 is stepped
        over, and the direction is a running toggle - working it out from the
        column's parity would fall out of step across that skip."""
        size, index = self.size, 0
        up = True
        col = size - 1
        while col >= 1:
            if col == 6:
                col -= 1
            rows = range(size - 1, -1, -1) if up else range(size)
            for row in rows:
                for c in (col, col - 1):
                    if c < 0 or self.fixed[row][c] or index >= len(bits):
                        continue
                    self.dark[row][c] = bits[index]
                    index += 1
            up = not up
            col -= 2

    def apply_mask(self, pattern):
        for row in range(self.size):
            for col in range(self.size):
                if not self.fixed[row][col] and _masked(pattern, row, col):
                    self.dark[row][col] ^= 1

    def write_format(self, mask):
        """The 15 format bits, copied to both places that read them, plus the
        version bits a symbol of 7 or more carries."""
        value = _format_bits(LEVEL_L_BITS, mask)
        size = self.size
        for i in range(15):
            bit = (value >> (14 - i)) & 1     # the highest bit goes first
            if i < 6:
                self.set(8, i, bit, False)
            elif i < 8:
                self.set(8, i + 1, bit, False)
            elif i == 8:
                self.set(7, 8, bit, False)
            else:
                self.set(14 - i, 8, bit, False)
            if i < 7:
                self.set(size - 1 - i, 8, bit, False)
            else:
                self.set(8, size - 15 + i, bit, False)
        self.set(size - 8, 8, 1, False)
        if self.version >= 7:
            value = _version_bits(self.version)
            for i in range(18):
                bit = (value >> i) & 1
                near = size - 11 + i % 3
                self.set(near, i // 3, bit, False)
                self.set(i // 3, near, bit, False)


def _format_bits(level, mask):
    """BCH(15,5) of the level and mask, against the pattern's own XOR mask."""
    data = (level << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | (rem & 0x3FF)) ^ 0x5412


def _version_bits(version):
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | (rem & 0xFFF)


def _masked(pattern, row, col):
    """Whether this mask should flip the module: the eight conditions."""
    if pattern == 0:
        return (row + col) % 2 == 0
    if pattern == 1:
        return row % 2 == 0
    if pattern == 2:
        return col % 3 == 0
    if pattern == 3:
        return (row + col) % 3 == 0
    if pattern == 4:
        return (row // 2 + col // 3) % 2 == 0
    if pattern == 5:
        return row * col % 2 + row * col % 3 == 0
    if pattern == 6:
        return (row * col % 2 + row * col % 3) % 2 == 0
    return ((row + col) % 2 + row * col % 3) % 2 == 0


def _line_penalty(line):
    """Rule one: runs of five or more of one colour. Rule three: an eye-like
    pattern with four light modules beside it."""
    score, run, prev = 0, 1, line[0]
    for value in line[1:]:
        if value == prev:
            run += 1
            continue
        if run >= 5:
            score += 3 + (run - 5)
        run, prev = 1, value
    if run >= 5:
        score += 3 + (run - 5)
    eye = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    for start in range(len(line) - 10):
        window = line[start:start + 11]
        if window == eye or window == eye[::-1]:
            score += 40
    return score


def _penalty(grid):
    size = len(grid)
    score = 0
    for i in range(size):
        score += _line_penalty(grid[i])                            # rows
        score += _line_penalty([row[i] for row in grid])           # columns
    for row in range(size - 1):                                    # rule two
        for col in range(size - 1):
            value = grid[row][col]
            if (value == grid[row][col + 1] == grid[row + 1][col]
                    == grid[row + 1][col + 1]):
                score += 3
    dark = sum(sum(row) for row in grid)                           # rule four
    percent = dark * 100 // (size * size)
    return score + 10 * (abs(percent - 50) // 5)


def _render(text, version, pattern):
    """One symbol at a fixed version and mask; the penalty rules are what
    decide between the eight masks, so this is the whole encoding."""
    stream = _bits(_interleave(version, _codewords(text, version)))
    grid = _Grid(version)
    grid.build_patterns()
    grid.place_data(stream)
    grid.apply_mask(pattern)
    grid.write_format(pattern)
    return [list(row) for row in grid.dark]


def encode(text):
    """The symbol for `text` as rows of 0/1, dark module first, in the
    smallest version it fits and the mask that scores best."""
    text = str(text)
    version = _pick(text)
    if version is None:
        raise ValueError(f"too long for a QR code: {len(text)} bytes")
    return min((_render(text, version, pattern) for pattern in range(8)),
               key=lambda grid: _penalty(grid))
