"""Wrap assets/logo.png into a Windows .ico - pure stdlib, no ImageMagick.

Vista and later accept a PNG-compressed icon entry, so a modern icon is just
the PNG's own bytes dropped behind a one-line ICONDIR header. That gives the
Windows desktop shortcut and the PyInstaller ``--icon` flag a real .ico
without any image library in the build.

Run with:  python tools/make_ico.py [png] [ico]
Defaults to the app logo beside it in the source tree.
"""

import pathlib
import struct
import sys

# the icon entry stores each edge as one byte; 0 stands for 256
_MAX_EDGE = 256


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


def build(png_path=None, ico_path=None):
    root = pathlib.Path(__file__).resolve().parents[1]
    png = pathlib.Path(png_path) if png_path else (
        root / "src" / "fligchess" / "assets" / "logo.png")
    ico = pathlib.Path(ico_path) if ico_path else (
        root / "src" / "fligchess" / "assets" / "logo.ico")
    data = png.read_bytes()
    ico.write_bytes(png_to_ico(data))
    return ico


def main(argv):
    out = build(argv[1] if len(argv) > 1 else None,
                argv[2] if len(argv) > 2 else None)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
