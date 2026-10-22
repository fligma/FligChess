"""Regenerate the root index.json from the bots/ folder.

index.json is what the in-app bot updater downloads: one entry per bot
file with name, version and sha256. It is rebuilt by the GitHub workflow
whenever bots change, and this script produces the identical file locally.

Version numbers live in `bots/<file>.meta.json` (written by the CI when a
bot changes; see .github/workflows/build.yml). A brand-new bot with no
meta file is registered at version 1.

Usage:  python tools/make_bot_index.py [--bump fligbot_classic.py]
"""

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOTS = ROOT / "bots"


def app_version():
    """The current APP_VERSION, read straight from const.py. Parsed rather
    than imported so this build tool never pulls pygame into a plain Python
    environment; an unreadable file yields "" (which the updater reads as
    'no minimum app version')."""
    try:
        src = (ROOT / "src" / "fligchess" / "const.py").read_text(
            encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', src)
    return m.group(1) if m else ""


def bot_name(path):
    """Import a bot file and read the name of the Bot subclass it defines."""
    sys.path.insert(0, str(ROOT / "src"))
    from fligchess.core.bot_api import Bot
    spec = importlib.util.spec_from_file_location("idx_bot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for obj in vars(module).values():
        if (isinstance(obj, type) and issubclass(obj, Bot)
                and obj is not Bot and getattr(obj, "name", None)):
            return obj.name
    raise SystemExit(f"{path.name}: no Bot subclass with a name found")


def bot_modes(path):
    """The game shapes a bot accepts, read off its MODES class attribute.

    Shipped bots report this from the loaded class anyway; carrying it in the
    index too lets the browser label a remote bot the app has not downloaded
    yet. Empty when the file predates MODES."""
    sys.path.insert(0, str(ROOT / "src"))
    from fligchess.core.bot_api import Bot
    spec = importlib.util.spec_from_file_location("idx_bot_modes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for obj in vars(module).values():
        if (isinstance(obj, type) and issubclass(obj, Bot)
                and obj is not Bot and getattr(obj, "name", None)):
            return list(getattr(obj, "MODES", ()) or ())
    return []


def meta_path(path):
    return BOTS / (path.stem + ".meta.json")


def read_version(path):
    try:
        return json.loads(meta_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(path, meta):
    meta_path(path).write_text(json.dumps(meta, indent=2) + "\n",
                               encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bump", metavar="FILE", help="increment this bot's version")
    args = ap.parse_args()

    files = sorted(p for p in BOTS.glob("*.py") if not p.name.startswith("_"))
    app = app_version()
    entries = []
    for path in files:
        meta = read_version(path)
        if meta is None:
            meta = {"version": 1}          # brand-new bot: ships at v1
        elif path.name == args.bump:
            meta["version"] = meta.get("version", 1) + 1
        write_meta(path, meta)
        raw = path.read_bytes()
        entries.append({
            "name": bot_name(path),
            "file": path.name,
            "version": int(meta["version"]),
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "app": app,                     # minimum app version that runs it
            "modes": bot_modes(path),       # game shapes the bot plays
        })

    index = {"repo": "fligma/FligChess", "bots": entries}
    (ROOT / "index.json").write_text(json.dumps(index, indent=2) + "\n",
                                     encoding="utf-8")
    for e in entries:
        print(f"{e['file']}: {e['name']} v{e['version']}")
    print("index.json written")


if __name__ == "__main__":
    main()
