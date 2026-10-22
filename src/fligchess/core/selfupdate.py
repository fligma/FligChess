"""The in-app "Update client" check-and-swap.

A thin wrapper over core.releases (what is the newest build) and
core.appinstall (put it on disk, then relaunch). It mirrors the bot
updater's shape: a `check()` that compares versions and an
`update_and_relaunch()` that installs over the running folder and hands off
to a detached helper that finishes the swap once this process exits.

Free of pygame and the app so it runs on a worker thread and under test.
The swap only makes sense for a packaged build: running from source has no
self-contained executable to replace, so it reports that instead.
"""

import sys

from fligchess import version as _version
from fligchess.core import appinstall, releases


def check():
    """Compare this build with the newest published release.

    Returns {"current", "latest", "newer", "published_at", "asset"}; asset
    is the platform download chosen from the release, or None. Raises
    ValueError (network or no release) for the caller to show."""
    release = releases.latest_release()
    asset = releases.pick_zip_asset(release)
    latest = release.get("version", "")
    return {
        "current": _version.APP_VERSION,
        "latest": latest,
        "newer": _version.is_newer(latest, _version.APP_VERSION),
        "published_at": release.get("published_at", ""),
        "asset": asset,
    }


def update_and_relaunch(progress=None):
    """Download the newest build into the running app's folder and swap it
    in, then relaunch. Returns a short status line describing what happened.

    Only a packaged build can replace itself; from source this returns a
    note and touches nothing."""
    if not getattr(sys, "frozen", False):
        return "Not a packaged build - update the source instead."
    result = check()
    if not result["newer"]:
        return f"Already on the newest version ({result['current']})."
    # install into the folder holding the running executable
    from fligchess.core.paths import APP_ROOT
    release = {"version": result["latest"], "assets": [result["asset"]]}
    exe = appinstall.install_release(release, str(APP_ROOT), progress=progress)
    # stage the freshly unpacked binary beside the live one and hand off:
    # the appinstall swap helper waits for us to exit, moves the new exe
    # into place, launches it, and cleans itself up.
    import os
    import shutil
    from pathlib import Path
    live = Path(sys.executable)
    staged = Path(str(live) + (".new.exe" if os.name == "nt" else ".new"))
    new_exe = Path(exe)
    if new_exe.resolve() != live.resolve():
        shutil.copy2(str(new_exe), str(staged))
    appinstall.relaunch_after_swap(os.getpid())
    return f"Updating to {result['latest']}; relaunching..."
