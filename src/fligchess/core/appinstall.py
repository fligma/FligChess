"""Installing and swapping a FligChess release on disk.

Shared by the pygame installer (installer/installer.py) and the in-app
"Update client" path (core/selfupdate). Everything here is stdlib only, no
pygame and no app state, so it runs headless and under test.

The build workflow publishes a per-platform zip carrying the packaged app
plus its ``bots/`, ``config/` and ``assets/` folders. ``install_release`
downloads that zip and unpacks it into a folder; ``desktop_shortcut` drops
an icon pointing at the new executable; ``relaunch_after_swap` writes a
tiny detached helper that waits for the old process to die, swaps the
binary in and starts the new one - the trick that lets an app replace its
own running executable.
"""

import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from fligchess.core import releases

# the executable names inside a packaged build, per platform
_WIN_EXE = "FligChess.exe"
_LINUX_EXE = "FligChess"


def default_install_dir():
    """Where a per-user install lands when the caller does not choose.

    Windows uses %LOCALAPPDATA%\\FligChess; elsewhere a share folder under
    the home directory. No admin rights needed either way."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" /
                                                     "Local")
        return str(Path(base) / "FligChess")
    return str(Path.home() / ".local" / "share" / "FligChess")


def _extract_zip(archive, dest_dir):
    """Unpack one zip under ``dest_dir`, guarding against a member that
    would escape the folder (a "../" or absolute path)."""
    dest = Path(dest_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if dest not in target.parents and target != dest:
                raise ValueError(f"unsafe path in zip: {member.filename}")
        zf.extractall(dest)


def _find_exe(root):
    """The app executable inside an extracted tree: the build might nest
    everything under a FligChess/ folder, so search the top two levels."""
    want = _WIN_EXE if os.name == "nt" else _LINUX_EXE
    direct = Path(root) / want
    if direct.is_file():
        return direct
    for child in Path(root).glob(f"*/{want}"):
        return child
    # a bare fall-back: any single top-level executable of that name
    for cand in Path(root).rglob(want):
        return cand
    return direct               # not found; caller can report the path


def _make_executable(exe):
    if os.name != "nt":
        try:
            mode = exe.stat().st_mode
            exe.chmod(mode | 0o111)
        except OSError:
            pass


def install_release(release, dest_dir, progress=None, token=None):
    """Download the platform zip and unpack it into ``dest_dir`.

    ``progress` is called as progress(done, total) while the archive
    downloads. ``token` is an optional GitHub token forwarded to the
    downloader (the installer's token field). Creates ``bots/`, ``config/`
    and ``assets/` if the zip left them out, marks the linux binary
    executable, and returns the path of the app executable."""
    system = releases.current_system()
    asset = releases.pick_zip_asset(release, system)
    if asset is None:
        raise ValueError(f"release has no {system} download")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if asset["name"].lower().endswith(".zip"):
        tmp = Path(tempfile.mkdtemp(prefix="fliginstall-"))
        archive = tmp / asset["name"]
        releases.download(asset, archive, progress=progress, token=token)
        _extract_zip(archive, dest_dir)
        shutil_rmtree(tmp)
    else:
        # a bare single-file build: drop it straight in
        exe = dest_dir / (_WIN_EXE if system == "windows" else _LINUX_EXE)
        releases.download(asset, exe, progress=progress, token=token)
        _make_executable(exe)
        for sub in ("bots", "config", "assets"):
            (dest_dir / sub).mkdir(parents=True, exist_ok=True)
        return str(exe)
    exe = _find_exe(dest_dir)
    _make_executable(exe)
    for sub in ("bots", "config", "assets"):
        (dest_dir / sub).mkdir(parents=True, exist_ok=True)
    return str(exe)


def shutil_rmtree(path):
    """Remove a scratch tree, ignoring anything already gone."""
    import shutil
    shutil.rmtree(str(path), ignore_errors=True)


def _desktop_path():
    if os.name == "nt":
        base = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(base) / "Desktop"
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if out:
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


def desktop_shortcut(exe, icon=None):
    """Put a FligChess launcher on the desktop pointing at ``exe`.

    Windows: shell out to PowerShell to write a real .lnk with an icon.
    Linux: write an XDG .desktop entry and symlink it onto the desktop.
    Returns the path of whatever was created, or None on any failure (a
    missing desktop or a locked-down shell must never crash an install)."""
    exe = Path(exe).resolve()
    try:
        if os.name == "nt":
            return _windows_shortcut(exe, icon)
        return _linux_shortcut(exe, icon)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _windows_shortcut(exe, icon):
    desk = _desktop_path()
    lnk = desk / "FligChess.lnk"
    icon = icon or str(exe.parent / "logo.ico")
    install_dir = str(exe.parent)
    script = f'''
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut("{lnk}")
$s.TargetPath = "{exe}"
$s.WorkingDirectory = "{install_dir}"
$s.IconLocation = "{icon}"
$s.Description = "FligChess"
$s.Save()
'''.strip()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   check=True, timeout=30, creationflags=flags)
    return str(lnk)


def _linux_shortcut(exe, icon):
    install_dir = str(exe.parent)
    icon = icon or str(exe.parent / "assets" / "logo.png")
    apps = Path.home() / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / "fligchess.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=FligChess\n"
        "Comment=Chess app with embeddable bots\n"
        f"Exec={exe}\n"
        f"Path={install_dir}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Game;BoardGame;\n", encoding="utf-8")
    try:
        desk = _desktop_path()
        desk.mkdir(parents=True, exist_ok=True)
        link = desk / "fligchess.desktop"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(entry)
        os.chmod(entry, 0o755)
    except OSError:
        pass
    return str(entry)


def relaunch_after_swap(pid):
    """Hand off a self-replace: a detached helper waits for ``pid` to exit,
    finishes copying the new build over the old one, launches it, and then
    deletes itself.

    The caller (core/selfupdate) has already unpacked the new files next to
    the running app; the helper runs the last step the OS will not let a
    live process do to itself - overwrite its own executable. Returns True
    if the helper was started."""
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False) is False:
        return False              # running from source: nothing to swap
    if os.name == "nt":
        return _win_swap(pid, exe)
    return _nix_swap(pid, exe)


def _win_swap(pid, exe):
    helper = Path(tempfile.gettempdir()) / "fligchess_swap.bat"
    new = exe.parent / "FligChess.new.exe"
    lines = [
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>NUL | find /I "{pid}" >NUL',
        "if %ERRORLEVEL%==0 (",
        "  timeout /t 1 /nobreak >NUL",
        "  goto wait",
        ")",
        f'if exist "{new}" (',
        f'  move /Y "{new}" "{exe}" >NUL',
        ")",
        f'start "" "{exe}"',
        f'del "%~f0"',
    ]
    helper.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(["cmd", "/c", str(helper)], creationflags=flags,
                     close_fds=True)
    return True


def _nix_swap(pid, exe):
    new = str(exe) + ".new"
    script = (
        f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done; "
        f'[ -f "{new}" ] && mv -f "{new}" "{exe}" && chmod +x "{exe}"; '
        f'nohup "{exe}" >/dev/null 2>&1 &'
    )
    subprocess.Popen(["sh", "-c", script], start_new_session=True,
                     close_fds=True)
    return True


def system_name():
    """A friendly OS label for install dialogs."""
    return "Windows" if os.name == "nt" else platform.system() or "Linux"
