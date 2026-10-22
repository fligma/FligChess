"""GitHub Releases lookup shared by the installer and "Update client".

The build workflow publishes a rolling release (tag ``fligchessV<version>`)
carrying a per-platform zip: ``FligChess-windows.zip` and
``FligChess-linux.zip`. This module asks the Releases API for the newest one
and hands back the pieces the caller needs - a parsed version, the asset to
fetch for this OS, and a downloader that checks the sha256 the release
carries (when present).

Like bot_updater, it is free of pygame and the app. The repo slug and the
optional private-repo token come from bot_updater so both updaters read the
same config/updater.json / GITHUB_TOKEN / FLIGBOT_TOKEN. Public releases
work with no token at all; a caller can also pass one explicitly (the
installer's optional token field does this), which wins over config/env.
"""

import hashlib
import json
import urllib.error
import urllib.request

from fligchess.core import bot_updater
from fligchess import version as _version

TIMEOUT = 20          # seconds per network operation
API_ROOT = "https://api.github.com"


def _api(url, token):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "FligChess-updater",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _asset_view(asset):
    """Trim one release asset down to the fields we act on. GitHub stores
    the digest as ``sha256:<hex>` or a bare hex string depending on API
    version; normalise to the ``sha256:<hex>` form the rest uses."""
    digest = asset.get("digest") or ""
    if digest and not digest.startswith("sha256:"):
        digest = "sha256:" + digest
    return {
        "name": asset.get("name", ""),
        "size": asset.get("size", 0),
        "digest": digest,
        "url": asset.get("browser_download_url") or asset.get("url", ""),
    }


def latest_release(token=None):
    """The newest release as ``{version, tag, published_at, assets:[...]}`.

    version is the tag with the ``fligchessV` prefix stripped. Raises
    ValueError on a network failure or an unexpected response shape, in the
    same style as bot_updater.fetch_index. An explicit ``token` (typed by
    the user, say) takes precedence over config/updater.json and the env
    vars, which is how a rate-limited anonymous lookup gets retried.
    """
    token = token or bot_updater.access_token()
    url = f"{API_ROOT}/repos/{bot_updater.repo_slug()}/releases/latest"
    try:
        raw = _api(url, token)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("repo needs a token: put {\"token\": ...} in "
                             "config/updater.json") from None
        if exc.code == 404:
            raise ValueError("no releases published yet") from None
        raise ValueError(f"server said HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise ValueError(f"no connection ({exc.reason})") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ValueError("release JSON is not valid") from None
    if not isinstance(data, dict) or "tag_name" not in data:
        raise ValueError("release JSON has no tag")
    tag = data.get("tag_name", "")
    ver = tag
    if ver.startswith(_version.TAG_PREFIX):
        ver = ver[len(_version.TAG_PREFIX):]
    elif ver.startswith("v"):
        ver = ver[1:]
    assets = [_asset_view(a) for a in data.get("assets", []) or []]
    return {
        "version": ver,
        "tag": tag,
        "published_at": data.get("published_at", ""),
        "assets": assets,
    }


def current_system():
    """The platform key used to pick an asset: ``windows` or ``linux`."""
    import sys
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def pick_zip_asset(release, system=None):
    """The ``FligChess-<system>.zip` entry, falling back to a bare
    ``.exe` (windows) or an un-suffixed binary (linux) when a zip is not
    published. Returns None when nothing matches."""
    system = system or current_system()
    assets = release.get("assets", [])
    want_zip = f"fligchess-{system}.zip"
    for asset in assets:
        if asset["name"].lower() == want_zip:
            return asset
    # fall back to the single-file build for this platform
    if system == "windows":
        for asset in assets:
            if asset["name"].lower().endswith(".exe") and "installer" not in \
                    asset["name"].lower():
                return asset
    else:
        for asset in assets:
            name = asset["name"].lower()
            if name.startswith("fligchess") and not name.endswith(
                    (".zip", ".appimage", ".desktop")) and "installer" not in name:
                return asset
    return None


def download(asset, dest, progress=None, token=None):
    """Stream ``asset` to the path ``dest`, verifying the ``sha256:<hex>`
    digest when the release carries one. ``progress` is called as
    progress(done_bytes, total_bytes) as the body arrives. Returns the path;
    raises ValueError on a network failure or a checksum mismatch. ``token`
    follows the same rule as latest_release: explicit first, then config."""
    token = token or bot_updater.access_token()
    url = asset.get("url") or ""
    if not url:
        raise ValueError("asset has no download url")
    req = urllib.request.Request(url, headers={"User-Agent": "FligChess-updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    digest = asset.get("digest") or ""
    want = digest[len("sha256:"):] if digest.startswith("sha256:") else digest
    hasher = hashlib.sha256() if want else None
    dest = str(dest)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    if hasher is not None:
                        hasher.update(chunk)
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
    except urllib.error.URLError as exc:
        raise ValueError(f"download failed ({exc.reason})") from None
    if hasher is not None and hasher.hexdigest() != want:
        raise ValueError("checksum mismatch")
    return dest
