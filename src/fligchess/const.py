"""FligChess constants: the single home for text, defaults and tuning.

Everything the program shows or acts on that is not logic lives here: the
version, the player-type tokens, the settings schema and its defaults, the
look-and-feel numbers, the board colours and the fixed menu / dialog labels.
Keeping the words in one file means the later screens (the LAN wizard, the
server-stats panel, the bot browser) add their labels here rather than
scattering literals through the code.

This module is a leaf: it imports only the XP theme palette, never the app,
so both ``ui.settings`` and ``fligchess`` can depend on it without a cycle.
"""

import pathlib

from fligchess.ui import xp

# ------------------------------------------------------------------ version
# The one place the app's version lives. Bump APP_VERSION by hand for a
# release; the build workflow tags "fligchessV" + APP_VERSION, so repeated
# pushes before a bump refresh the same release's assets.
APP_VERSION = "1.0.0"
TAG_PREFIX = "fligchessV"


def release_tag():
    """The GitHub release tag this build publishes to."""
    return f"{TAG_PREFIX}{APP_VERSION}"


def parse_version(text):
    """Split a dotted version into a comparable tuple of ints. Anything the
    release tag carries besides digits (the "fligchessV" prefix) is ignored.
    Non-numeric parts count as 0 so a malformed string never raises."""
    parts = []
    for chunk in (text or "").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest, current=None):
    """True when latest sorts above current (this build)."""
    return parse_version(latest) > parse_version(current or APP_VERSION)


# --------------------------------------------------------------- player seats
HUMAN, AI = "human", "ai"        # the two kinds of player a seat can be
# A third kind, never offered on the offline setup page: a seat held by the
# browser guest of a hosted game. It plays no engine and is not a local human,
# so fog treats it as invisible and clicks are gated to the host instead.
REMOTE = "remote"
SEATS = ("w", "b")               # one seat per colour, white first
NAME_LIMIT = 24                  # characters accepted in a player name

# --------------------------------------------------------------- settings rows
# Every setting the Options page offers and the values it accepts. Booleans
# toggle, lists cycle. Split into the groups the page draws.
GUI_SETTINGS = ("animation_speed", "capture_effects", "move_hints",
                "move_panel", "board_theme", "coordinates", "fullscreen")
SFX_SETTINGS = ("sound", "volume")
# How the app talks to other machines: the names it offers, whether the web
# and LAN servers speak HTTPS, the certificate's size and lifetime, a
# text-free action row that reissues that certificate on the spot, and a
# toggle that reveals the read-only developer section below.
CONNECT_SETTINGS = ("host_name", "client_name", "https", "password_after_join",
                    "cert_bits", "cert_days", "reissue_cert", "dev_mode")
# free text typed on the settings page rather than cycled through values
TEXT_SETTINGS = ("host_name", "client_name")
# rows that do something when clicked instead of holding a value
ACTION_SETTINGS = ("reissue_cert",)

# The Updater group on the Settings page: a masked Git token field, a "?" row
# that explains it, and an action that opens the bot browser. None of these
# are stored in settings.json - the token lives in config/updater.json and the
# browser talks to the repo directly.
UPDATER_SETTINGS = ("git_token", "token_help", "manage_bots")
TOKEN_HELP_TITLE = "Git token"
TOKEN_HELP_TEXT = (
    "Optional. A GitHub personal-access token lifts the anonymous\n"
    "API rate limit and lets the updater reach a private repo.\n"
    "It is stored in config/updater.json and sent only to\n"
    "github.com. Leave it blank to use public access.")
TOKEN_FIELD_TIP = "GitHub token used by the updater; leave blank for public access."

# The bot browser: a modal overlay opened by "Manage bots..." and by the Help
# menu's "Update bots...". It lists every bot (installed and available) with a
# state badge and per-row Download / Update / Delete buttons. These are the
# fixed words it paints; the numbers and names come from bot_updater.check().
BOT_BROWSER_TITLE = "Manage bots"
BOT_HEADINGS = ("Bot", "Local", "Remote", "Status")
BOT_STATE_LABELS = {"current": "installed",
                    "update": "update available",
                    "new": "not installed",
                    "local": "local only"}
BOT_BTN_DOWNLOAD = "Download"
BOT_BTN_UPDATE = "Update"
BOT_BTN_DELETE = "Delete"
BOT_BTN_CLOSE = "Close"
BOT_BTN_RETRY = "Try again"
BOT_BLOCKED_NOTE = "Update the app first"   # app-min-version gate text
BOT_LOADING = "Contacting the repository..."
BOT_EMPTY = "No bots are listed in the index."
BOT_NONE_LOCAL = "No bots installed."


def bot_app_blocked(app_min):
    """True when a bot's minimum app version is newer than this build, so its
    Download / Update must be refused until the app itself is updated. An
    absent requirement (empty) is always compatible - backward compatible."""
    return bool(app_min) and is_newer(app_min, APP_VERSION)

# The developer section, shown only while "dev mode" is on. Every key here is
# read-only: the app computes the value live (server state, certificate,
# on-disk paths, network and the far end of a LAN game) and paints it as plain
# text. They are grouped so each block gets its own caption on the page.
DEV_SERVER = ("dev_server", "dev_url", "dev_https", "dev_port",
              "dev_mode_kind", "dev_visitors", "dev_guest")
DEV_CERT = ("dev_cert_state", "dev_cert_bits", "dev_cert_days",
            "dev_cert_issued", "dev_cert_expiry", "dev_cert_hosts",
            "dev_cert_fp")
DEV_PATHS = ("dev_p_config", "dev_p_settings", "dev_p_cert", "dev_p_key",
             "dev_p_meta", "dev_p_bots", "dev_p_app")
DEV_NET = ("dev_net_ip", "dev_net_host", "dev_net_listen")
DEV_CLIENT = ("dev_cli_role", "dev_cli_url", "dev_cli_name",
              "dev_cli_color", "dev_cli_status")
DEV_SETTINGS = DEV_SERVER + DEV_CERT + DEV_PATHS + DEV_NET + DEV_CLIENT

# A single clickable toggle, revealed only while dev_mode is on, that shows
# or hides the "Bot Dev" menu screen. It is deliberately NOT part of the
# read-only DEV_SETTINGS diagnostics above: pick_setting returns early for
# those, so keeping this in its own group is what leaves it switchable.
DEV_TOGGLES = ("show_bot_dev",)

OPTIONS = {
    "animation_speed": ["slow", "normal", "fast"],
    "capture_effects": [True, False],
    "move_hints": [True, False],
    "move_panel": [True, False],
    "board_theme": ["brown", "green", "gray"],
    "coordinates": [True, False],
    "fullscreen": [True, False],
    "sound": [True, False],
    "volume": ["quiet", "normal", "loud"],
    "https": [True, False],
    "password_after_join": [True, False],
    "cert_bits": [1024, 2048, 4096],
    "cert_days": [90, 365, 825],
    "dev_mode": [True, False],
    "show_bot_dev": [True, False],
}

# Chosen on the game setup screens rather than in the options list. Only the
# rules are stored as a value of their own: which side is human and which is
# an engine is carried by "players", so nothing can say "Human vs AI" while
# the seats say otherwise. (The variants themselves are listed in
# fligchess.core.variants.)
PICKS = {
    "variant": ("classic", "fog", "minefield"),
}

DEFAULTS = {
    "variant": "classic",
    "animation_speed": "normal",
    "capture_effects": True,
    "move_hints": True,
    "move_panel": True,
    "board_theme": "brown",
    "coordinates": False,
    "fullscreen": False,
    "sound": True,
    "volume": "normal",
    "bot_config": {},
    "host_name": "",
    "client_name": "",
    "https": True,
    "password_after_join": True,
    "cert_bits": 2048,
    "cert_days": 825,
    "dev_mode": False,
    "show_bot_dev": False,
}

# Seconds a piece slide animation takes, per animation_speed setting.
ANIM_SECONDS = {"slow": 0.4, "normal": 0.22, "fast": 0.1}

# (light square, dark square) per board_theme setting.
THEMES = {
    "brown": ((181, 171, 150), (120, 87, 65)),
    "gray": ((170, 170, 170), (115, 115, 115)),
    "green": ((233, 224, 203), (74, 148, 88)),
}

# ------------------------------------------------------------- look & feel
ASSETS_DIR = pathlib.Path(__file__).resolve().parent / "assets"
SPRITE = 16               # native sprite size in pieces.png
DEFAULT_WINDOW = (760, 560)
MIN_WINDOW = (420, 340)
BOT_THINK_DELAY = 0.35    # seconds the bot "thinks" before its move lands

# SysFont tries each name in order, so the XP look survives on systems
# without Tahoma (Linux) by falling back to a similar sans face
UI_FONT = "tahoma,verdana,segoeui,dejavusans"
TITLEBAR_H = 28           # blue Luna caption strip
MENUBAR_H = 22            # File/View/Game/Help strip
STATUSBAR_H = 24          # bottom grip bar

# Windows XP "Luna" palette, aliased under the names the draw code uses
COL_BG = xp.FACE                   # window face (#ECE9D8)
COL_TEXT = xp.TEXT                 # Tahoma black
COL_MUTED = xp.TEXT_MUTED
COL_SELECTED = (180, 205, 235, 170)  # board square tint, light XP blue
COL_LAST_MOVE = (165, 205, 60, 120)  # chess.com-style olive tint
COL_CHECK = (240, 80, 70, 110)
COL_DOT = (70, 95, 70, 160)
COL_FOG = (24, 26, 40, 195)         # a square the viewer may not see
COL_MINE = (52, 52, 56)             # a revealed mine: body and spikes
COL_ACCENT_LIT = (28, 78, 158)     # deep blue for values / marks
COL_GOLD = (198, 148, 0)           # turn glow, material advantage

# pieces.png rows: 0 = black, 1 = white. columns in K Q B N R P order.
_SPRITE_COLS = {"k": 0, "q": 1, "b": 2, "n": 3, "r": 4, "p": 5}

# chess.com style material count: pawn 1, knight/bishop 3, rook 5, queen 9
PIECE_VALUES = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9}

# ---------------------------------------------------------------- app states
MENU, SETTINGS, GAME = "menu", "settings", "game"
# the two setup screens reached through Play, in order: the rules, then the
# players who play under them
VARIANT, PLAYERS = "variant", "players"
# the LAN hosting wizard, in order: pick how many play, then the settings,
# then the waiting room that holds the game until the host presses Play
LANMODE, HOST, LANWAIT = "lanmode", "host", "lanwait"
# the screen reached through Join LAN: the address of the game to connect to
JOIN = "join"
# the developer-only screen reached through the File menu: bot API docs and
# the (read-only) source of every installed bot
BOTDEV = "botdev"
BACK = "Back"                  # the last row of the rules screen

# Nothing stores which arrangement is being played: the two player seats are
# the answer, and SEAT_MODE reads the arrangement back off them while
# MODE_LABELS says it out loud in the caption and on the player page. Swapping
# the seats therefore changes the answer with nobody having to keep a copy.
MODE_LABELS = {"human_vs_ai": "Human vs AI", "ai_vs_ai": "AI vs AI",
               "human_vs_human": "Human vs Human",
               "ai_vs_human": "AI vs Human"}
SEAT_MODE = {(HUMAN, HUMAN): "human_vs_human", (HUMAN, AI): "human_vs_ai",
             (AI, AI): "ai_vs_ai", (AI, HUMAN): "ai_vs_human"}
TYPE_LABELS = {HUMAN: "Human", AI: "Computer"}   # the player type dropdown
# what the line says when the cursor rests on the Back button instead of a
# rule: it must explain the button you are pointing at, not the row above it
BACK_NOTE = "Go back to the previous step."

# ------------------------------------------------------------------- menus
# the main menu items, top to bottom. "Web LAN" and "Host LAN" merged into a
# single "LAN" wizard: one flow that picks the mode, the settings, and whether
# a browser is offered (the old split was two names for one server).
MENU_ITEMS = ["Play Offline", "LAN", "Join LAN", "Settings", "Quit"]

# the in-game menu bar: each caption with its dropdown entries, where None
# is a separator line
MENU_DEFS = (("File", ("Export game history...", None,
                       "Settings...", None, "Exit", "Bot Dev")),
             ("View", ("Fullscreen", "Move history panel", "Coordinates",
                       "Move hints", "Capture effects", "Flip board",
                       None, "Server stats...")),
             ("Game", ("New Game", "Restart lobby...", None, "Undo",
                        None, "Resign", "End Game")),
             ("Help", ("Update bots...", "Update client...", None,
                        "About FligChess")))

# View menu label -> settings key it shows or hides
VIEW_TOGGLES = {"Move history panel": "move_panel",
                "Coordinates": "coordinates",
                "Move hints": "move_hints",
                "Capture effects": "capture_effects"}

# ---------------------------------------------------------------- LAN wizard
# One hosting flow: pick how many play, set the game and site options, then
# wait in the lobby and press Play once a player has taken the seat. The old
# "Web LAN" and "Host LAN" menu names collapsed into this; the "offer in
# browser" toggle is what used to be the choice between the two items.
LAN_MODE_TITLE = "How many players?"
LAN_MODE_LABELS = {2: "2 players", 4: "4 players"}
LAN_MODE_NOTE = {2: "Two armies on one board: white and black.",
                 4: "Four armies on a cross board: white, black, red and blue."}
LAN_MODE_BACK = BACK
LAN_SETUP_TITLE = "Host a game"
LAN_SETUP_GROUP = "Game and site settings"
LAN_SETUP_NOTE = ("left/right choose, click or space a field to type")
LAN_NEXT = "Next"
LAN_HOST_TITLE = "Waiting for a player"
LAN_HOST_WAITING = "Waiting for a player to join..."
LAN_HOST_READY = "A player is ready - press Play."
# a cross waiting room lists every seat instead: one line per army, holding
# the name its player gave or the reason the seat is still empty
LAN_HOST_WAITING4 = "Waiting for %d of %d players to join..."
LAN_SEAT_LINE = "%s  %s"
LAN_SEAT_COMPUTER = "Computer"
LAN_SEAT_WAITING = "waiting (%s)"
LAN_SEAT_HOST = "You"
LAN_PLAY = "Play"
LAN_BACK = "Back"
# the spectator tally the host sees: on its own line in the waiting room and
# tagged onto the status row once the game is played
LAN_WATCHING = "%d watching"
STATUS_WATCHING = "  \u00b7  %d watching"
# the settings row captions the host page draws
LAN_ROW_RULES = "Rules"
LAN_ROW_PLAY = "Players"
LAN_ROW_YOU = "You play"
LAN_ROW_YOURNAME = "Your name"
LAN_ROW_HOSTPLAYS = "Host plays"
LAN_ROW_BROWSER = "Offer in browser"
LAN_ROW_PASSWORD = "Password"
LAN_ROW_SPECTATE = "Spectators"
LAN_ROW_LOCK = "Keep locked"
LAN_ROW_PORT = "Port"
# a cross game adds one row per army the host does not hold, naming who plays
# that side: a built-in engine, a seat opened for browsers or one for apps
LAN_ROW_SEAT = "%s plays"
LAN_SEAT_VALUES = ("ai", "web", "app")
LAN_SEAT_LABELS = {"ai": "Computer", "web": "Web player", "app": "App player"}
# the value each settings row shows on the right: a combo toggles between two
# words, the colour row names a side, and the "Host plays" row falls back to
# a human when no engine is seated
VALUE_ON = "on"
VALUE_OFF = "off"
VALUE_WHITE = "White"
VALUE_BLACK = "Black"
# what a joined app says about where its game stands, driven by the snapshot
CLIENT_WAIT = "Waiting for the host to start..."
CLIENT_YOUR_MOVE = "Your move"
CLIENT_CHECK = " - check!"
CLIENT_OPPONENT = "Opponent to move"
# a cross game names the army on turn instead, and waits for several players
CLIENT_WAIT4 = "Waiting for players to join..."
CLIENT_ARMY_TO_MOVE = "%s to move"

# ---------------------------------------------------------------- roles
# The name each participant sees of themselves, shown in the window caption
# and the browser tab. The web page and the app both read the seat's role off
# the snapshot; these are the fixed words they paint.
ROLE_HOST = "Host"
ROLE_PLAYER = "Player"
ROLE_SPECTATOR = "Spectator"
# the side a chat line is attributed to, next to a small coloured pawn
CHAT_COLORS = ("w", "b", "r", "bl")     # white, black, red, blue armies

# ------------------------------------------------- four-player cross board
# The dedicated four-army engine (core.board4) and its cross layout. The
# bounding grid is 14x14 with the four 3x3 corners cut out; each army keeps a
# full standard set along the home and pawn ranks of its arm of the cross.
# Armies are named by id here and everywhere the cross is drawn, so a single
# word is written once.
CROSS_SIZE = 14                 # the bounding grid is CROSS_SIZE x CROSS_SIZE
ARMY_IDS = ("w", "b", "r", "bl")          # the fixed turn order, white first
ARMY_LABELS = {"w": "White", "b": "Black", "r": "Red", "bl": "Blue"}
# the join page's side picker: an auto seat (whatever side the host opened
# first) or one of the four armies. A two-player host simply has no red or
# blue to give, and its server seats the joiner on the first free seat.
JOIN_COLOR_VALUES = ("auto",) + ARMY_IDS
JOIN_COLOR_LABELS = dict({"auto": "Auto"}, **ARMY_LABELS)
# a tint per army, for the web board's home-arm highlight and turn indicator
ARMY_TINT = {"w": (240, 240, 240), "b": (40, 40, 40),
             "r": (200, 60, 50), "bl": (60, 110, 200)}
# pieces.png carries white (row 1) and black (row 0); pieces2.png carries the
# two extra pallets - red on the top row, blue on the bottom
SPRITE_SHEET2 = "pieces2.png"
SPRITE2_ROWS = {"r": 0, "bl": 1}
FOUR_MODE_LABEL = "4 players"           # the caption/PGN name of a cross game
CROSS_OFF = "#"               # a snapshot cell that is not a playable square
# the offline player page's toggle between a two-side and a cross game
OFFLINE_PLAYERS_BUTTON = "Players: %d"

# ------------------------------------------------------- server stats panel
# The host's "View server stats" dialog: a scrollable read-only table of every
# connected session and the shared chat log, headed by a summary of the server
# itself. The numbers come from Host.stats_snapshot(); these are the fixed
# words it paints. Plain HTTP cannot measure ICMP, so round-trip and loss are
# reported as approximations derived from poll timing and errored requests.
STATS_TITLE = "Server stats"
STATS_MENU = "Server stats..."
STATS_BUTTON = "Stats"                 # the waiting-room button that opens it
STATS_CLOSE = "Close"
# the summary line captions shown above the client table
STATS_UPTIME = "Uptime"
STATS_PORT = "Port"
STATS_STATUS = "Status"
STATS_MODE_WEB = "Browser + LAN"
STATS_MODE_LAN = "LAN only"
# the per-session table headings (name, role, address, requests, approx RTT,
# error rate); the last-seen time and user agent ride on the row's second line
STATS_HEADINGS = ("Name", "Role", "Address", "Reqs", "RTT ms~", "Err %")
STATS_CLIENTS = "Connected sessions"
STATS_CHAT = "Message history"
STATS_NO_CLIENTS = "No one has connected yet."
# status badges derived from the live snapshot, mirroring the web roles
STATS_STATUS_WAITING = "waiting for a player"
STATS_STATUS_PLAYING = "playing"
STATS_STATUS_OVER = "finished"
# the server role token -> the word the panel shows for that connection
STATS_ROLE_LABELS = {"guest": ROLE_PLAYER, "viewer": ROLE_SPECTATOR,
                     "auth": "signed in"}
# a note under the table saying plainly these two figures are estimates
STATS_APPROX_NOTE = ("RTT and error rate are approximations over HTTP;")
STATS_APPROX_NOTE2 = ("there is no ICMP ping or true packet loss to measure.")
# one client row's second line: the agent, the bytes it moved and how long ago
STATS_ROW_DETAIL = "agent {agent}  -  {bytes} B  -  seen {seen}s ago"

# ----------------------------------------------------------- dialog labels
# The fixed captions the app's dialogs and panels paint, shared so a word is
# written once: the generic buttons, the share (QR) dialog the hosting flow
# offers, and the in-app chat panel.
BTN_OK = "OK"
BTN_CLOSE = "Close"
DIALOG_COPY_TITLE = "Copy"           # the title of the clipboard notice
COPY_DONE = "Copied to the clipboard:"
COPY_FAIL = "Could not copy; no clipboard here."
LAN_SHARE = "Share"                  # the waiting-room button that reopens it
SHARE_COPY_LINK = "Copy link"
SHARE_COPY_ADDR = "Copy address"
SHARE_RESTART = "Restart"
SHARE_PASSWORD = "Password: %s"
# the docked in-app chat panel
CHAT_TITLE = "Chat"
CHAT_SEND = "Send"
CHAT_PLACEHOLDER = "type, press enter"
CHAT_FAIL_TEXT = "Message not sent: no contact."
