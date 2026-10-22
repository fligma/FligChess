"""GitHub-backed bot updater.

The repository publishes an `index.json` listing every shippable bot with
its file name, version and sha256 (see tools/make_bot_index.py; the build
workflow regenerates and commits it whenever bots change). This module
fetches that index, compares it with the local `bots/` folder and
downloads what is missing or stale.

Endpoints are derived from the repo slug:
    https://raw.githubusercontent.com/<repo>/main/<path>

For private repos (or to dodge rate limits) a token can be provided in
`config/updater.json` ({"token": "...", "repo": "owner/name", "auto_check":
true}) or via the GITHUB_TOKEN / FLIGBOT_TOKEN environment variables; it is
sent as an `Authorization: Bearer` header. The repo slug itself can be
overridden the same way, so self-hosted forks work without touching the app.
config/updater.json may be edited by hand, and the Settings page's Updater
field writes just the token through save_token() (leaving repo/auto_check).
"""

import hashlib
import json
import os
import urllib.error
import urllib.request

from fligchess.core.paths import bots_dir, config_dir

DEFAULT_REPO = "fligma/FligChess"
INDEX_PATH = "index.json"
TIMEOUT = 15          # seconds per network operation


def _settings_path():
    return config_dir() / "updater.json"


def load_settings():
    """Read config/updater.json; damaged or missing files read as {}."""
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def repo_slug():
    return load_settings().get("repo") or DEFAULT_REPO


def access_token():
    return (load_settings().get("token")
            or os.environ.get("FLIGBOT_TOKEN")
            or os.environ.get("GITHUB_TOKEN") or "")


def save_token(token):
    """Merge a Git token into config/updater.json, preserving repo/auto_check.
    An empty token clears the stored one (env vars still apply). Returns the
    resulting settings dict."""
    data = load_settings()
    if token:
        data["token"] = token
    else:
        data.pop("token", None)
    try:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return data


def _raw_url(path):
    return f"https://raw.githubusercontent.com/{repo_slug()}/main/{path}"


def _get(url, token):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def fetch_index():
    """Download index.json; returns the parsed dict or raises ValueError."""
    token = access_token()
    try:
        raw = _get(_raw_url(INDEX_PATH), token)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("repo needs a token: put {\"token\": ...} in "
                             "config/updater.json") from None
        raise ValueError(f"server said HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise ValueError(f"no connection ({exc.reason})") from None
    try:
        index = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ValueError("index.json is not valid JSON") from None
    if not isinstance(index, dict) or not isinstance(index.get("bots"), list):
        raise ValueError("index.json has no bot list")
    return index


def probe(path):
    """Import just this bot file and read its advertised class attributes;
    returns (version, modes, name) - all None when the file is missing or
    fails to load. `modes` is the class's MODES tuple (bots written before
    the mode API existed report ("2p",), the class default)."""
    import importlib.util
    from fligchess.core.bot_api import Bot
    if not path.is_file():
        return (None, None, None)
    try:
        spec = importlib.util.spec_from_file_location(
            "fligchess_probe_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return (None, None, None)
    for obj in vars(module).values():
        if (isinstance(obj, type) and issubclass(obj, Bot)
                and obj is not Bot and getattr(obj, "name", None)):
            modes = tuple(getattr(obj, "MODES", ("2p",)) or ("2p",))
            return (getattr(obj, "version", None), modes,
                    getattr(obj, "name", None))
    return (None, None, None)


def local_version(path):
    """Just the version half of probe(); None when unreadable."""
    return probe(path)[0]


def bundled_versions():
    """{file: version} recorded at build time (config/bundled_bots.json),
    i.e. the bot versions this app release shipped with."""
    try:
        data = json.loads((config_dir() / "bundled_bots.json")
                          .read_text(encoding="utf-8"))
        return {b.get("file"): b.get("version") for b in data.get("bots", [])}
    except (OSError, ValueError, AttributeError):
        return {}


def check():
    """Compare remote index with the local bots/ folder.

    Returns {"repo", "up_to_date", "bots": [{name, file, remote, local,
    hash, current}]}; `current` is true when the local file already carries
    the listed hash. Raises ValueError (network or bad index) to the caller.
    """
    index = fetch_index()
    directory = bots_dir()
    entries = []
    for entry in index["bots"]:
        name, fname = entry.get("name"), entry.get("file")
        if not name or not fname:
            continue
        path = directory / fname
        digest = ("sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                  if path.is_file() else None)
        version, modes, _ = probe(path) if path.is_file() else (None, None, None)
        if modes is None:
            listed = entry.get("modes")
            modes = tuple(listed) if isinstance(listed, list) else None
        entries.append({
            "name": name,
            "file": fname,
            "remote": int(entry.get("version", 0)),
            "local": version,
            "modes": modes,
            "hash": entry.get("sha256", ""),
            "app": entry.get("app", ""),
            "current": digest == entry.get("sha256"),
        })
    return {"repo": repo_slug(),
            "bundled": bundled_versions(),
            "up_to_date": all(b["current"] for b in entries),
            "bots": entries}


def _record_version(fname, remote):
    """Note a bot's version in config/bot_versions.json after a download."""
    versions_path = config_dir() / "bot_versions.json"
    try:
        versions = json.loads(versions_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        versions = {}
    versions[fname] = remote
    try:
        versions_path.write_text(json.dumps(versions, indent=2) + "\n",
                                 encoding="utf-8")
    except OSError:
        pass


def download_bot(bot):
    """Fetch one listed bot, verify its sha256, write it under bots/ and
    record its version. `bot` is one entry from check()['bots']. Returns a
    one-line result and never raises, so a per-row action can report itself.
    Any locally modified file of the same name is overwritten."""
    token = access_token()
    directory = bots_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        raw = _get(_raw_url(f"bots/{bot['file']}"), token)
    except Exception as exc:  # noqa: BLE001 - report, keep the caller alive
        return f"{bot['name']}: download failed ({exc})"
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if bot.get("hash") and digest != bot["hash"]:
        return f"{bot['name']}: checksum mismatch, skipped"
    try:
        (directory / bot["file"]).write_bytes(raw)
    except OSError as exc:
        return f"{bot['name']}: could not write ({exc})"
    _record_version(bot["file"], bot.get("remote", 0))
    return f"{bot['name']}: updated to v{bot.get('remote', 0)}"


def delete_bot(fname):
    """Remove a bot file from the local bots/ folder; True when a file was
    actually deleted. Touches nothing else."""
    path = bots_dir() / fname
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def apply_updates(bots):
    """Download every bot flagged not `current`; verify the hash, write the
    file and record its version. Returns a list of human-readable result
    lines. Any locally modified file with the same name is overwritten -
    that is how updates land."""
    return [download_bot(bot) for bot in bots if not bot.get("current")]
