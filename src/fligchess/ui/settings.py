"""Persistent user settings, stored in the `config/` folder next to the app.

The Settings page only carries how the program looks and sounds; who plays
which side is decided before a game starts, on the setup screens, and lives
under "players" - one seat per colour.

    {"w": {"type": "human", "bot": None,              "name": "", "config": {}},
     "b": {"type": "ai",    "bot": "fligbot classic 2p", "name": "", "config": {...}}}

`config` holds one bot's own CONFIG values, so two seats on the same engine
can play it at different difficulties. Each bot's last used values are also
kept under "bot_config", one dict per bot name, so a bot picked later starts
where you left it.

The setting names, their accepted values and the defaults all live in
`fligchess.const`; this module only reads, migrates and writes them.
"""

import json
from pathlib import Path

from fligchess.core.paths import config_dir
from fligchess.const import (HUMAN, AI, REMOTE, SEATS, NAME_LIMIT,
                             GUI_SETTINGS, SFX_SETTINGS, CONNECT_SETTINGS,
                             TEXT_SETTINGS, ACTION_SETTINGS, UPDATER_SETTINGS,
                             DEV_SERVER, DEV_CERT, DEV_PATHS, DEV_NET,
                             DEV_CLIENT, DEV_SETTINGS, DEV_TOGGLES, OPTIONS,
                             PICKS, DEFAULTS, ANIM_SECONDS, THEMES)

SETTINGS_PATH = config_dir() / "settings.json"
# settings from earlier builds, migrated once on first load
LEGACY_PATH = Path.home() / ".fligchess" / "settings.json"


def new_seat(player=HUMAN, bot=None, config=None):
    """One player seat, fresh each call so seats are never shared."""
    return {"type": player, "bot": bot, "name": "", "config": dict(config or {})}


def default_players():
    """The starting arrangement: you play White, an engine plays Black."""
    return {"w": new_seat(), "b": new_seat(AI)}


def clean_seat(seat):
    """A stored seat with every key it must carry, nothing it must not."""
    seat = seat if isinstance(seat, dict) else {}
    kind = seat.get("type")
    return new_seat(player=kind if kind in (HUMAN, AI) else HUMAN,
                    bot=seat.get("bot"), config=seat.get("config")) | {
        "name": str(seat.get("name") or "")[:NAME_LIMIT]}


def clean_players(players):
    """Both seats from a stored dict, defaults for anything missing."""
    players = players if isinstance(players, dict) else {}
    return {color: clean_seat(players.get(color)) for color in SEATS}


def players_from_legacy(data):
    """Builds before seats existed stored one opponent name per side; carry
    that arrangement over instead of quietly resetting it."""
    out = {}
    for color, key in (("w", "white_opponent"), ("b", "opponent")):
        named = data.get(key)
        out[color] = new_seat(AI, named) if named not in (None, "Human") \
            else new_seat()
    return out


class Settings(dict):
    """A dict of setting values with helpers for cycling enum-style options."""

    @classmethod
    def load(cls):
        settings = cls({k: (dict(v) if isinstance(v, dict) else v)
                        for k, v in DEFAULTS.items()})
        settings["players"] = default_players()   # never share the defaults
        data = None
        for path in (SETTINGS_PATH, LEGACY_PATH):
            try:
                if path.exists():
                    data = json.loads(path.read_text())
                    break
            except (OSError, ValueError):
                continue
        if not isinstance(data, dict):
            return settings
        for key, value in data.items():
            if key == "bot_config" and isinstance(value, dict):
                settings["bot_config"] = value
            elif key == "players" and isinstance(value, dict):
                settings["players"] = clean_players(value)
            elif key in TEXT_SETTINGS:
                settings[key] = str(value or "")[:NAME_LIMIT]
            elif key in PICKS and value in PICKS[key]:
                settings[key] = value
            elif key in OPTIONS and value in OPTIONS[key]:
                settings[key] = value
        if not isinstance(data.get("players"), dict):
            settings["players"] = players_from_legacy(data)
        return settings

    def save(self):
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(dict(self), indent=2))
        except OSError:
            pass  # settings are non-critical; keep running without them

    def cycle(self, key):
        """Toggle a boolean or advance a list option to its next value."""
        choices = OPTIONS[key]
        self[key] = choices[(choices.index(self[key]) + 1) % len(choices)]

    @staticmethod
    def kind(key):
        """How a settings row behaves: it holds free text, toggles, cycles a
        list, fires an action, or is the Back button."""
        if key in TEXT_SETTINGS:
            return "text"
        if key in ACTION_SETTINGS:
            return "action"
        if isinstance(OPTIONS[key][0], bool):
            return "bool"
        return "choice"

    def display(self, key):
        value = self[key]
        if isinstance(value, bool):
            return "on" if value else "off"
        return value

    # ----------------------------------------------------------- bot options

    def bot_options(self, bot_name, config):
        """Current saved value for every CONFIG key of a bot, defaults filled."""
        saved = self["bot_config"].get(bot_name, {})
        return {key: saved.get(key, spec["default"])
                for key, spec in config.items()}

    @property
    def anim_seconds(self):
        return ANIM_SECONDS[self["animation_speed"]]
