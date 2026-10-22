"""Filesystem locations that live next to the app and stay user-editable.

During development these resolve to the repository root. When packaged with
PyInstaller they resolve to the folder containing the .exe, so `bots/` and
`config/` can still be edited after a build.
"""

import sys
from pathlib import Path

if getattr(sys, "frozen", False):          # running a PyInstaller bundle
    APP_ROOT = Path(sys.executable).resolve().parent
else:                                      # running from source
    APP_ROOT = Path(__file__).resolve().parents[3]


def bots_dir():
    """Folder scanned for bot .py files (not created; absence is fine)."""
    return APP_ROOT / "bots"


def config_dir():
    """Folder for user-editable config files; created on first use."""
    folder = APP_ROOT / "config"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder
