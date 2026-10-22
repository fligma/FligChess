"""Wrap assets/logo.png into a Windows .ico - pure stdlib, no ImageMagick.

Vista and later accept a PNG-compressed icon entry, but a shell or a
desktop shortcut only renders reliably when the .ico also carries the
classic small sizes as raw 32bpp BMP entries. So this tool decodes the
logo PNG (zlib + the five row filters - stdlib only), nearest-neighbour
scales it to 16/32/48, and writes those plus the untouched PNG as one
multi-image .ico. That gives the Windows desktop shortcut, the exe icon
(PyInstaller ``--icon``) and the taskbar a real, standard icon without any
image library in the build.

Run with:  python tools/make_ico.py [png] [ico]
Defaults to the app logo beside it in the source tree.
"""

import pathlib
import struct
import sys
import zlib

# the icon entry stores each edge as one byte; 0 stands for 256
_MAX_EDGE = 256
# the standard small sizes generated as BMP entries beside the native PNG
_BMP_SIZES = (16, 32, 48)


def png_size(data):
    """(width, height) read from a PNG's IHDR chunk."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    return struct.unpack(">II", data[16:24])


def png_to_ico(data):
    """ICONDIR + one entry + the PNG bytes, for a single-size icon. Vista
    and later read a PNG-compressed entry, so the image data is the PNG
    itself; the entry header is a fixed 16 bytes after a 6-byte ICONDIR."""
    width, height = png_size(data)
    edge = lambda n: 0 if n >= _MAX_EDGE else n      # noqa: E731 (byte form)
    header = struct.pack("<HHH", 0, 1, 1)             # reserved, type, count
    image_offset = len(header) + 16                   # ICONDIR + one entry
    entry = struct.pack("<BBBBHHII", edge(width), edge(height), 0, 0, 1, 32,
                        len(data), image_offset)
    return header + entry + data


def decode_png(data):
    """RGBA scanlines of a non-interlaced 8-bit RGB/RGBA PNG, pure stdlib.
    Covers the form the app's logo assets are written in; anything else
    raises ValueError so the caller falls back to a raw-PNG entry.
    Returns (width, height, [bytearray of w*4 per row])."""
    hdr = struct.unpack(">IIBBBBB", data[16:29])
    w, h, depth, ctype, _comp, _filt, interlace = hdr
    if interlace or depth != 8 or ctype not in (2, 6):
        raise ValueError("need a non-interlaced 8-bit RGB/RGBA PNG")
    channels = 4 if ctype == 6 else 3
    idat = bytearray()
    pos = 8
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(bytes(idat))
    stride = w * channels
    rows, prev = [], bytearray(stride)
    for y in range(h):
        chunk = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        ftype = raw[y * (stride + 1)]
        if ftype == 1:                              # Sub: left pixel
            for i in range(channels, stride):
                chunk[i] = (chunk[i] + chunk[i - channels]) & 255
        elif ftype == 2:                            # Up: pixel above
            for i in range(stride):
                chunk[i] = (chunk[i] + prev[i]) & 255
        elif ftype == 3:                            # Average of the two
            for i in range(stride):
                a = chunk[i - channels] if i >= channels else 0
                chunk[i] = (chunk[i] + ((a + prev[i]) >> 1)) & 255
        elif ftype == 4:                            # Paeth predictor
            for i in range(stride):
                a = chunk[i - channels] if i >= channels else 0
                c = prev[i - channels] if i >= channels else 0
                b = prev[i]
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                chunk[i] = (chunk[i] + pr) & 255
        elif ftype != 0:
            raise ValueError("bad PNG row filter")
        prev = chunk
        if channels == 4:
            rows.append(bytearray(chunk))
        else:                                       # RGB: add full alpha
            row = bytearray(w * 4)
            for x in range(w):
                row[x * 4:x * 4 + 3] = chunk[x * 3:x * 3 + 3]
                row[x * 4 + 3] = 255
            rows.append(row)
    return w, h, rows


def scale_rgba(w, h, rows, size):
    """Nearest-neighbour rescale of RGBA rows to size x size. The logo is
    pixel art, so hard steps look intentional instead of blurry."""
    out = []
    for y in range(size):
        src = rows[min(h - 1, y * h // size)]
        dst = bytearray(size * 4)
        for x in range(size):
            sx = min(w - 1, x * w // size) * 4
            dst[x * 4:x * 4 + 4] = src[sx:sx + 4]
        out.append(dst)
    return out


def bmp_entry(size, rgba_rows):
    """One classic ICO image: 32bpp BGRA bottom-up pixels under a
    BITMAPINFOHEADER whose height counts the image twice (pixels + the
    1-bit AND mask), plus an all-zero mask meaning "alpha decides"."""
    pixels = bytearray()
    for row in reversed(rgba_rows):                 # bottom-up
        for x in range(size):
            r, g, b, a = row[x * 4:x * 4 + 4]
            pixels += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         len(pixels) + len(mask), 0, 0, 0, 0)
    return header + bytes(pixels) + mask


def build_ico(data):
    """The full .ico bytes: BMP entries for the standard small sizes plus
    the original PNG at its native size. Falls back to the single PNG
    entry when the image cannot be decoded here."""
    w = png_size(data)[0]
    try:
        pw, ph, rows = decode_png(data)
    except ValueError:
        return png_to_ico(data)
    images = [(s, bmp_entry(s, scale_rgba(pw, ph, rows, s)))
              for s in _BMP_SIZES if s != pw]
    images.append((pw, data))                       # the native PNG image
    edge = lambda n: 0 if n >= _MAX_EDGE else n      # noqa: E731 (byte form)
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blob = bytearray(), bytearray()
    for size, payload in images:
        entries += struct.pack("<BBBBHHII", edge(size), edge(size), 0, 0,
                               1, 32, len(payload), offset + len(blob))
        blob += payload
    return header + entries + blob


def build(png_path=None, ico_path=None):
    root = pathlib.Path(__file__).resolve().parents[1]
    png = pathlib.Path(png_path) if png_path else (
        root / "src" / "fligchess" / "assets" / "logo.png")
    ico = pathlib.Path(ico_path) if ico_path else (
        root / "src" / "fligchess" / "assets" / "logo.ico")
    ico.write_bytes(build_ico(png.read_bytes()))
    return ico


def main(argv):
    out = build(argv[1] if len(argv) > 1 else None,
                argv[2] if len(argv) > 2 else None)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
