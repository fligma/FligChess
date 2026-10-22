"""FligChess - a chess app built on pygame.

Run with:  python -m fligchess.fligchess   (from the src/ directory)
      or:  python src/fligchess/fligchess.py
"""

import argparse
import ctypes
import ctypes.util
import math
import pathlib
import random
import socket
import sys
import textwrap
import threading
import time
import urllib.parse

# Allow running this file directly, not just as a module.
if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pygame
import pygame.scrap        # clipboard for the share dialog's copy buttons

try:                      # real window tricks: borderless, move, resize
    from pygame._sdl2 import video as _sdl2
except ImportError:
    _sdl2 = None

from fligchess.core import (ChessGame, Move, Board, WHITE, BLACK, san,
                            Position, GameInfo, bot_allows, load_bots,
                            variants, WHITE_WINS, BLACK_WINS, DRAW)
from fligchess.core import bot_updater
from fligchess.core import selfupdate
from fligchess.core import bot_docs
from fligchess.core.game import Game4, ARMY_NAMES, _sig4, DRAW4
from fligchess.core import board4 as _board4
from fligchess.core.san4 import san4 as _san4
from fligchess.core.board4 import (ARMIES as _ARMY_IDS, WHITE as _A_WHITE,
                                   BLACK as _A_BLACK, RED as _A_RED,
                                   BLUE as _A_BLUE, SIZE as _CROSS_SIZE,
                                   playable as _cross_playable,
                                   sq_name as _cross_sq_name,
                                   kind_of as _cross_kind, army_of as _cross_army,
                                   Move as Move4)
from fligchess import const
from fligchess.const import (APP_VERSION, SPRITE, DEFAULT_WINDOW, MIN_WINDOW,
                             BOT_THINK_DELAY, UI_FONT, TITLEBAR_H, MENUBAR_H,
                             STATUSBAR_H, COL_BG, COL_TEXT, COL_MUTED,
                             COL_SELECTED, COL_LAST_MOVE, COL_CHECK, COL_DOT,
                             COL_FOG, COL_MINE, COL_ACCENT_LIT, COL_GOLD,
                             ASSETS_DIR, _SPRITE_COLS, PIECE_VALUES, MENU,
                             SETTINGS, GAME, VARIANT, PLAYERS, HOST, JOIN,
                             LANMODE, LANWAIT, BOTDEV, BACK, MODE_LABELS,
                             SEAT_MODE, TYPE_LABELS, BACK_NOTE)
from fligchess.core.board import sq_name
from fligchess.core.paths import bots_dir, config_dir, APP_ROOT
from fligchess.net import qr
from fligchess.net.host import Host, free_port, local_ip
from fligchess.net.client import Client
from fligchess.ui import xp
from fligchess.ui import sound
from fligchess.ui.effects import make_capture_effect, make_blast_effect
from fligchess.ui.settings import (Settings, OPTIONS, PICKS, THEMES, AI, HUMAN,
                                   REMOTE, SEATS, NAME_LIMIT, GUI_SETTINGS,
                                   SFX_SETTINGS, CONNECT_SETTINGS,
                                   TEXT_SETTINGS, clean_players, SETTINGS_PATH,
                                   DEV_SERVER, DEV_CERT, DEV_PATHS, DEV_NET,
                                   DEV_CLIENT, DEV_SETTINGS, DEV_TOGGLES)
from fligchess.net import cert

# ----------------------------------------------------------------- look & feel
# The fixed constants, defaults, colours and labels live in fligchess.const
# and are imported into this module's namespace near the top, so the draw and
# logic code below keeps referring to them by their short names.


def _piece_color(piece):
    return WHITE if piece.isupper() else "b"


def _load_sdl_dll():
    """The very SDL2 library pygame links against, for absolute mouse and
    work-area queries (only reachable through ctypes).  Works on Windows
    (SDL2.dll next to pygame), Linux (bundled libSDL2-2.0.so.0 in the
    wheel, or the system copy) and macOS."""
    if sys.platform == "win32":
        names = ["SDL2.dll"]
    elif sys.platform == "darwin":
        names = ["libSDL2.dylib", "libSDL2-2.0.0.dylib"]
    else:
        names = ["libSDL2-2.0.so.0", "libSDL2-2.0.so", "libSDL2.so"]
    pkg = pathlib.Path(pygame.__file__).resolve().parent
    for name in names:                      # 1. pygame's own bundled copy
        path = pkg / name
        if path.exists():
            try:
                return ctypes.CDLL(str(path))
            except OSError:
                pass
    for name in names:                      # 2. loader default search
        try:
            return ctypes.CDLL(name)
        except OSError:
            pass
    try:                                    # 3. system library lookup
        found = ctypes.util.find_library("SDL2") or ctypes.util.find_library(
            names[0])
        if found:
            return ctypes.CDLL(found)
    except (OSError, ValueError):
        pass
    return None


def ease_out(t):
    """Smooth 0->1 curve for piece slides."""
    return t * t * (3 - 2 * t)


def _blend_tint(owners, alpha=64):
    """The RGBA a union view paints on a tile every listed army can see: the
    average of their tints, so a lone owner shows that team's hue and several
    blend toward a shared one. An unknown owner reads as neutral grey."""
    cols = [const.ARMY_TINT.get(o, (128, 128, 128)) for o in owners] \
        or [(128, 128, 128)]
    n = len(cols)
    return (sum(c[0] for c in cols) // n, sum(c[1] for c in cols) // n,
            sum(c[2] for c in cols) // n, alpha)


def _parse_square(name):
    """A board square from its coordinate text ("e2"), or None if malformed.
    Guest moves arrive as text over HTTP and are validated with this."""
    if not isinstance(name, str) or len(name) < 2:
        return None
    file_ch, rank_ch = name[0], name[1]
    if not ("a" <= file_ch <= "h") or not ("1" <= rank_ch <= "8"):
        return None
    return ord(file_ch) - ord("a"), int(rank_ch) - 1


def _parse_cross(name):
    """A cross-board square from its coordinate text. Files run a-n and ranks
    1-14, so a four-player move arrives as e.g. "a14". Returns (x, y) or None."""
    if not isinstance(name, str) or len(name) < 2:
        return None
    file_ch = name[0]
    try:
        rank = int(name[1:])
    except ValueError:
        return None
    if not ("a" <= file_ch <= "n"):
        return None
    x = ord(file_ch) - ord("a")
    y = rank - 1
    if not (0 <= x < _CROSS_SIZE and 0 <= y < _CROSS_SIZE):
        return None
    return x, y


def _rows_to_fen(rows, turn):
    """A display FEN from the snapshot's eight board strings (rank 8 first,
    '.' for empty). Castling and en passant are not carried, but a client only
    needs the pieces in place to draw them and to follow the server's legal
    destination list."""
    out = []
    for row in rows:
        line, empty = "", 0
        for ch in row:
            if ch == ".":
                empty += 1
            else:
                if empty:
                    line += str(empty)
                    empty = 0
                line += ch
        if empty:
            line += str(empty)
        out.append(line)
    return "/".join(out) + " %s - - 0 1" % (turn or "w")


def _client_status_line(snap, client_color):
    """One line telling a LAN client where its game stands."""
    if snap.get("status") == "waiting":
        return const.CLIENT_WAIT
    to_move = "w" if client_color == WHITE else "b"
    if snap.get("turn") == to_move:
        return const.CLIENT_YOUR_MOVE + (const.CLIENT_CHECK
                                        if snap.get("check") else "")
    return const.CLIENT_OPPONENT


def _client_status_line4(snap, army):
    """One line telling a joining app where its cross game stands: whose
    turn it is by army, not by colour, and that the lobby still waits for
    every seat to be filled before the host presses Play."""
    if snap.get("status") == "waiting":
        return const.CLIENT_WAIT4
    if snap.get("turn") == army:
        return const.CLIENT_YOUR_MOVE + (const.CLIENT_CHECK
                                        if snap.get("check") else "")
    return const.CLIENT_ARMY_TO_MOVE % const.ARMY_LABELS.get(
        snap.get("turn"), snap.get("turn") or "?")


def _qr_scale(window_w, window_h):
    """Pixels per QR module, so the share code stays inside the window with
    room for its caption, link and button around it."""
    return max(3, min(8, (min(window_w, window_h) - 150) // 33))


def _set_win_app_id():
    """Claim our own Windows taskbar button. Without an explicit
    AppUserModelID the shell groups the window under whatever launched it
    and paints the generic pygame feather in the taskbar; with one, the
    icon set by pygame.display.set_icon is the icon the taskbar shows.
    Windows only, and never fatal."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Fligma.FligChess")
    except (OSError, AttributeError):
        pass


# ------------------------------------------------------------------ the app


class ChessApp:
    def __init__(self, args=None):
        _set_win_app_id()             # before the window exists: taskbar icon
        pygame.init()
        pygame.display.set_caption("FligChess")
        self._set_os_icon()
        self.args = args if args is not None else argparse.Namespace()

        # discover bot scripts in the bots/ folder; an engine seat can only
        # name one of these (before settings load, so a saved name validates)
        self.bots = load_bots(bots_dir())

        self.settings = Settings.load()
        # who plays each side, edited on the setup screens and kept in the
        # same dict so saving the settings saves the players with it
        self.players = self.settings["players"] = self._clean_players(
            self.settings["players"])
        self.sfx = sound.Sfx()
        self.sfx.set_enabled(self.settings["sound"])
        self.sfx.set_volume(self.settings["volume"])

        self.fullscreen = self.settings["fullscreen"]
        self.window = DEFAULT_WINDOW
        self._win = None                 # single live SDL window wrapper
        self.screen = self._set_mode()
        self._dll = _load_sdl_dll()
        self._maximized = False
        self._last_cap = None          # (secs, pos) for titlebar double-click
        self._drag = None              # active drag operation, see handle_event
        self._win_pending = None       # geometry to apply outside the pump
        self._applied_pos = self._applied_size = None   # drag dedupe

        self.sheet = pygame.image.load(ASSETS_DIR / "pieces.png").convert_alpha()
        # the cross board's two extra pallets (red, blue) ride on a second
        # sheet; a build that ships without it degrades to the two-army look
        try:
            self.sheet2 = pygame.image.load(
                ASSETS_DIR / const.SPRITE_SHEET2).convert_alpha()
        except (pygame.error, OSError):
            self.sheet2 = self.sheet
        # the two-pawn mark shown in the About box; a clean 2x keeps the
        # pixel art crisp without needing the board's sprite scaling
        self.logo = pygame.transform.scale(
            pygame.image.load(ASSETS_DIR / "logo.png").convert_alpha(), (48, 48))

        self.state = MENU
        self.menu_items = list(const.MENU_ITEMS)
        self.menu_index = 0
        # the setup screens reached through Play
        self.variant_index = variants.VARIANTS.index(self.settings["variant"])
        self.players_index = 0         # keyboard focus on the player page
        self._drop = None              # [seat, row, cursor] whose list is open
        self._text_focus = None        # (seat, row) being typed into
        self.settings_rows = self._build_settings_rows()
        self._settings_scroll = 0      # pixels scrolled up the settings page
        self.settings_return = MENU    # where Close takes you: MENU or GAME
        self.settings_index = 0
        self._settings_text = None     # which text row is being typed
        # ---- Bot Dev screen: the bot API docs and installed source, read-only
        self._botdev_tab = 0           # 0 = Docs, 1 = Installed bots
        self._botdev_scroll = 0        # pixels scrolled up the text panel
        self._botdev_bot = None        # bot whose source the second tab shows

        self.game = None
        # ---- four-player cross board (M8): a dedicated, self-contained branch
        # that drives a Game4 and paints the cross, kept apart from the proven
        # two-player path so nothing below that line changes behaviour.
        self.four = False              # a four-army cross game is up
        self.seats4 = {}               # army id -> {"type", "name"} (HUMAN/AI)
        self.host_army = _A_WHITE      # the army a hosted cross game's host holds
        self.guest_army = _A_BLACK     # the army its one remote guest controls
        # the seats a hosted game opened at its server: a list of
        # {"color", "kind"} for a cross game, None for the lone shared seat
        # of a two-player game (or a game with no human seats at all)
        self.host_seats = None
        self._ai_t = 0.0               # cross-seat random-fill think timer
        self._cross_surface = None     # cached cross checkerboard
        self.variant = variants.create(self.settings["variant"])
        self._fog = frozenset()        # squares the current viewer can't see
        self._tint = {}                # union view: shown square -> seeing armies
        self._viewer = None            # whose eyes the screen shows
        # ---- web hosting: live only while a hosted game is up
        self.hosting = False           # are we the host of a running game?
        self.host = None               # the net.host.Host, once started
        self.host_color = WHITE        # the side the local player controls
        self.guest_color = BLACK       # the side the browser guest controls
        self.host_form = self._new_host_form()   # the Web LAN setup page
        self._host_events = []           # move/blast journal the page animates
        self._host_ev_seq = 0            # never reset: clients track it
        self.host_index = 0            # keyboard focus on the setup page
        self._host_text = None         # which text field is being typed
        self.qr_open = False           # show the share-link (QR) dialog?
        # the waiting room holds the game until the host presses Play: a
        # player may sit down (claim the seat) without the clock starting
        self.awaiting_start = False    # in LANWAIT, gate moves until Play
        self.lan_players = 2           # armies the LAN wizard was set for
        self.offline_players = 2       # armies an offline game was set for
        # the four cross seats an offline game edits, kept apart from the two
        # colour seats above so the two-player page is untouched; each is
        # {"type": HUMAN/AI, "name": str} (a cross AI names a gate-allowed
        # engine)
        self.offline_seats = self._default_offline_seats()
        self._qr_surface = None        # cached QR bitmap
        self._qr_url = None            # the url the cached bitmap is for
        self._qr_close = self._qr_cap = None
        # ---- chat: shared between the host app and its guests / LAN peers
        self.lan_mode = False          # hosted app-to-app, no browser page
        self.chat_messages = []        # {"who", "text"}, oldest first
        self.chat_open = False         # show the in-game chat panel?
        self._chat_input = ""          # text being typed into the chat
        self._chat_focus = False        # chat input box has the keyboard
        self._chat_scroll = 0          # pixels scrolled up the message list
        self._chat_close = None        # close box of the open chat panel
        # ---- LAN client: joining another app's hosted game, no server here
        self.lan_client = False        # driving a game from a snapshot?
        self.client = None             # the net.client.Client once connected
        self.client_name = "Guest"     # the name this client plays under
        self._join_form = {"url": "", "name": "", "password": ""}
        self.join_index = 0            # keyboard focus on the join page
        self._join_text = None         # which join field is being typed
        self._client_since = 0         # newest chat sequence the client has read
        self._client_poll_t = 0.0      # seconds until the next snapshot poll
        self._client_status = ""       # last snapshot's status line
        self._client_dests = {}        # moves the server says are legal
        self._selname = None           # square name of the current selection
        self.client_color = BLACK      # the side this app plays in a LAN game
        self.client_army = None        # army a joining app holds in a cross game
        self.client_is_viewer = False  # a LAN peer told to spectate (read-only)
        self.bot = None                # live Bot instance playing black
        self.white_bot = None          # a second bot here means AI vs AI
        self.bot_color = BLACK         # the black bot always plays black
        self._think = None             # {"t", "box"} while a bot computes
        self._think4 = None            # the same, for a cross army's engine
        self.think_delay = BOT_THINK_DELAY
        self.open_menu = None          # index of the dropped-down menu bar item
        self.show_about = False
        self.about_pos = None          # dialogs remember where they were dragged
        self.flipped = False           # board orientation (white at bottom?)
        self.history = []            # {"san", "captured", "mover"} per ply
        # move review: one entry per ply, index 0 the start position, index k
        # the board after k moves; view_ply is None while following live
        self._review = []
        self.view_ply = None
        self.scroll_y = 0
        self.follow_latest = True
        self.selected = None
        self.selected_moves = []
        self.last_move = None
        self.pending_promotion = None
        self.anim = None
        self.effects = []
        self.show_over = False
        self.over_pos = None
        self.over_dismissed = False    # dialog closed on the caption X
        self.duel_ended = False        # an AI-vs-AI duel stopped by End Game
        self._over_close = self._over_cap = None
        self._about_close = self._about_cap = None
        self.message = None          # simple info box: (title, text)
        self.message_pos = None
        self._msg_close = self._msg_cap = None
        self.glow_t = 0.0
        self.report_path = None        # --report FILE: live PGN for tournaments
        self._reported_ply = -1
        self._end_t = 0.0              # seconds since the game finished
        self._update_avail = None      # bot names waiting an update, or None
        self._probe_box = None         # background auto-check, when enabled
        self._client_box = None        # {"note", "done"} while updating client
        # ---- Updater: the Git token is edited in memory before it lands in
        # config/updater.json, and the bot browser is a modal overlay listing
        # every bot (installed and available) with per-row actions.
        self._token_buf = None         # token being typed (None until focused)
        self._browser = None            # bot browser overlay state, or None
        self._browser_box = None        # {"entries", "error", "done"} worker
        self._browser_scroll = 0        # pixels scrolled up the bot list
        # the host's "View server stats" overlay: a read-only scrollable table
        # of every connected session plus the shared chat log, or None
        self._stats = None
        self._layout()
        self._apply_cli()
        self._start_update_probe()

    # ------------------------------------------------------ window & layout

    def _set_mode(self):
        flags = pygame.RESIZABLE
        if self.fullscreen:
            flags |= pygame.FULLSCREEN
        # drop the cached wrapper *before* set_mode, which may replace the
        # underlying SDL window and leave the wrapper dangling
        self._win = None
        screen = pygame.display.set_mode(self.window, flags)
        self.window = screen.get_size()
        self._apply_chrome()
        return screen

    def _apply_chrome(self):
        """Drop the system titlebar: the app paints and handles its own,
        XP style, for the whole window."""
        win = self._sdlwin(create=True)
        if win is None:
            return
        try:
            win.borderless = not self.fullscreen
        except Exception:
            pass

    def _set_os_icon(self):
        """Give the window the FligChess logo as its taskbar / alt-tab /
        caption icon, instead of the default pygame feather. Paired with
        _set_win_app_id, which is what actually pins this icon to the
        taskbar on Windows. Called before the mode is set so the icon is
        in place the moment the window appears; a missing or unreadable
        asset never blocks startup."""
        try:
            icon = pygame.image.load(ASSETS_DIR / "logo.png").convert_alpha()
            pygame.display.set_icon(icon)
        except (pygame.error, OSError):
            pass

    def _sdlwin(self, create=False):
        """The one live SDL window wrapper, reused for the whole session.
        Never make throwaway from_display_module() wrappers per call: when
        CPython finalizes one *during* pygame.event.get(), SDL tears down
        the wrapper mid-pump and the process dies with a native stack
        overflow - trivially hit by dragging the borderless caption.
        Returns None while fullscreen (already frameless by other means)."""
        if _sdl2 is None or (self.fullscreen and not create):
            return None
        if self._win is None:
            try:
                self._win = _sdl2.Window.from_display_module()
            except Exception:
                return None
        return self._win

    def windowed(self):
        return not self.fullscreen and self._sdlwin() is not None

    def global_mouse(self):
        """Desktop-wide cursor position: the only stable reference while a
        borderless window moves or resizes underneath the cursor.
        Returns None when the platform cannot answer (Wayland, dummy video
        driver, or no SDL loaded) so callers degrade instead of crashing."""
        if self._dll is None:
            return None
        try:
            fn = self._dll.SDL_GetGlobalMouseState
            fn.argtypes = [ctypes.POINTER(ctypes.c_int),
                           ctypes.POINTER(ctypes.c_int)]
            fn.restype = ctypes.c_int
            x, y = ctypes.c_int(), ctypes.c_int()
            if fn(ctypes.byref(x), ctypes.byref(y)) < 0:
                return None               # SDL_SetError: unsupported here
            return (x.value, y.value)
        except Exception:
            return None

    def display_bounds(self):
        """Per-monitor (x, y, w, h) boxes from SDL; [] when unavailable.
        Coordinates are virtual-desktop: monitors left of or above the
        primary have negative x / y, so the union covers every screen."""
        if self._dll is None:
            return []
        try:
            count = self._dll.SDL_GetNumVideoDisplays()
            fn = self._dll.SDL_GetDisplayBounds
            fn.argtypes = [ctypes.c_int, ctypes.c_void_p]
            fn.restype = ctypes.c_int
            rect = (ctypes.c_int * 4)()
            out = []
            for i in range(max(0, count)):
                if fn(i, ctypes.byref(rect)) == 0:
                    out.append(tuple(rect))
            return out
        except Exception:
            return []

    def _virtual_desktop(self):
        """Bounding box (x0, y0, x1, y1) covering *all* monitors; falls
        back to the primary display when SDL cannot describe the layout."""
        b = self.display_bounds()
        if b:
            return (min(x for x, _, _, _ in b),
                    min(y for _, y, _, _ in b),
                    max(x + w for x, _, w, _ in b),
                    max(y + h for _, y, _, h in b))
        w, h = pygame.display.get_desktop_sizes()[0]
        return (0, 0, w, h)

    def _current_display_size(self):
        """Pixel size of the monitor the window currently sits on, so
        fullscreen picks the right resolution on any screen."""
        b = self.display_bounds()
        cx = cy = None
        win = self._sdlwin(create=True)
        try:
            if win is not None:
                px, py = win.position
                cx = px + self.window[0] // 2
                cy = py + self.window[1] // 2
        except Exception:
            cx = None
        for x, y, w, h in b:
            if cx is not None and x <= cx < x + w and y <= cy < y + h:
                return (w, h)
        if b:
            return (b[0][2], b[0][3])
        return pygame.display.get_desktop_sizes()[0]

    # ------------------------------------------------------- monitors & CLI

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32),
                    ("rcMonitor", ctypes.c_long * 4),
                    ("rcWork", ctypes.c_long * 4),
                    ("dwFlags", ctypes.c_uint32),
                    ("szDevice", ctypes.c_wchar * 32)]

    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32),
                    ("DeviceName", ctypes.c_wchar * 32),
                    ("DeviceString", ctypes.c_wchar * 128),
                    ("StateFlags", ctypes.c_uint32),
                    ("DeviceID", ctypes.c_wchar * 34),
                    ("Key", ctypes.c_wchar * 128)]

    def monitor_names(self):
        """[(x, y, w, h, friendly name), ...] over the physical monitors.
        Friendly names (e.g. "DELL S2722QC") only resolve on Windows via
        user32; elsewhere the list degrades to SDL bounds with empty
        names, so name matching falls back to the primary display."""
        if sys.platform == "win32":
            try:
                boxes = self._win_monitors()
            except Exception:
                boxes = []
            if boxes:
                return boxes
        return [(x, y, w, h, "") for x, y, w, h in self.display_bounds()]

    def _win_monitors(self):
        user32 = ctypes.windll.user32
        seen = []
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_size_t,
                                    ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_long * 4),
                                    ctypes.c_ssize_t)

        def cb(hmon, _hdc, _lprc, _data):
            info = ChessApp._MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                x0, y0, x1, y1 = info.rcMonitor
                blob = [info.szDevice]
                for i in range(4):            # the device + its monitors
                    dd = ChessApp._DISPLAY_DEVICEW()
                    dd.cb = ctypes.sizeof(dd)
                    if not user32.EnumDisplayDevicesW(info.szDevice, i,
                                                      ctypes.byref(dd), 0):
                        break
                    # DeviceString is often just "Generic PnP Monitor";
                    # DeviceID carries the EDID vendor code (MONITOR\DEL...)
                    blob.append(dd.DeviceString)
                    blob.append(dd.DeviceID)
                seen.append((x0, y0, x1 - x0, y1 - y0, " ".join(blob)))
            return True

        keep = proc_t(cb)                     # must outlive the enumeration
        if not user32.EnumDisplayMonitors(0, None, keep, 0):
            return []
        return seen

    # friendly-name substring -> also match the EDID vendor code in DeviceID
    MONITOR_ALIASES = {"DELL": ("\\DEL",), "PHILIPS": ("\\PHL",),
                       "SAMSUNG": ("\\SAM",), "LG": ("\\LGD", "\\LGV",)}

    def find_monitor(self, wanted):
        """Box of the first monitor whose name or EDID vendor code matches
        `wanted` (case insensitive); the primary monitor if none matches."""
        boxes = self.monitor_names()
        needle = (wanted or "").upper()
        terms = self.MONITOR_ALIASES.get(needle, ()) + (needle,)
        for x, y, w, h, label in boxes:
            up = label.upper()
            if any(t in up for t in terms):
                return (x, y, w, h)
        return boxes[0][:4] if boxes else None

    def place_on_monitor(self, box):
        """Center the window on the given (x, y, w, h) monitor box."""
        win = self._sdlwin(create=True)
        if win is None or box is None:
            return
        x, y, w, h = box
        try:
            win.position = (x + (w - self.window[0]) // 2,
                            y + (h - self.window[1]) // 2)
        except Exception:
            pass

    def _apply_cli(self):
        """Command-line overrides, mostly for AI vs AI tournaments: bot
        names, rules variant, monitor choice, fullscreen without touching
        saved settings, pacing, thinking budget and automatic PGN
        reporting."""
        a = self.args
        black = getattr(a, "black", None)
        white = getattr(a, "white", None)
        # a named engine takes that seat; naming only one of them means the
        # other side is played by a human, whatever the saved seats were
        if black in self.bots:
            self._set_seat_bot("b", black)
            if white not in self.bots:
                self._set_seat_type("w", HUMAN)
        if white in self.bots:
            self._set_seat_bot("w", white)
        if getattr(a, "variant", None) in variants.VARIANTS:
            self.settings["variant"] = a.variant
        if getattr(a, "fast", False):
            self.think_delay = 0.05
        self.report_path = getattr(a, "report", None)
        box = self.find_monitor(a.monitor) if getattr(a, "monitor", None) else None
        fs = getattr(a, "fullscreen", False)
        if box and not self.fullscreen:
            self.place_on_monitor(box)     # seeds where fullscreen will land
        if fs:
            self.fullscreen = True
            self.window = tuple(box[2:4]) if box else self._current_display_size()
            self.screen = self._set_mode()
        elif box and self.fullscreen:
            # saved fullscreen already on: retarget it onto the wanted monitor
            self.window = tuple(box[2:4])
            self.screen = self._set_mode()
        self._layout()
        if getattr(a, "autoplay", False):
            if getattr(a, "players", 2) == 4:
                # every cross army is engine-driven by an engine that plays
                # this variant; with no lone human nobody is hidden, so the
                # duel is shown as a union view
                self.offline_players = 4
                self.variant = variants.create(self.settings["variant"],
                                               four=True)
                seats = self._cross_autoplay_seats()
                if any(not s.get("bot") for s in seats.values()):
                    # a duel seat with no allowed engine: refuse to run
                    self.message = (const.NO_ENGINE_TITLE, const.NO_ENGINE_MSG)
                    return
                self.start_game4(seats, viewer=None)
            else:
                self.start_game()

    def _cross_autoplay_seats(self):
        """The four cross seats for `--autoplay --players 4`: each named
        engine sits its own army, and `--white` names every army not given an
        engine of its own. An army whose name is blank, uninstalled, or will
        not play this mode falls back to the default engine for the mode - and
        to nothing where no engine plays it, so the caller refuses to start."""
        a = self.args
        mode = self._mode_token(4)
        named = {"w": getattr(a, "white", None), "b": getattr(a, "black", None),
                 "r": getattr(a, "red", None), "bl": getattr(a, "blue", None)}
        seats = {}
        for army in _ARMY_IDS:
            name = named.get(army) or getattr(a, "white", None)
            if name not in self.bots or not self._bot_allows(name, mode):
                name = self._default_engine(mode)
            # name the seat by its engine when one is seated, so the live
            # report says who plays each army and a tournament can score a
            # win by engine; an army with no engine keeps its colour word
            seats[army] = {"type": AI, "bot": name,
                           "name": name or const.ARMY_LABELS[army]}
        return seats

    def toggle_maximize(self):
        win = self._sdlwin()
        if win is None:
            return
        try:
            win.restore() if self._maximized else win.maximize()
            self._maximized = not self._maximized
        except Exception:
            pass

    def _layout(self):
        """Recompute every size from the current window (resize/fullscreen)."""
        w, h = self.window
        # chrome: caption + menu strip on top, grip status bar at the bottom
        self.chrome_top = TITLEBAR_H + MENUBAR_H
        self.status_h = self.chrome_top + max(18, min(30, int(h * 0.05)))
        self.bottom_h = STATUSBAR_H
        row_h = self.status_h - self.chrome_top
        # small buttons at the right end of the status row: flip always, plus
        # a share (QR) button and a chat button while a game is being hosted
        self.flip_btn = pygame.Rect(w - 34, self.chrome_top
                                    + (row_h - 22) // 2, 26, 22)
        self.qr_btn = self.flip_btn.move(-30, 0)
        self.chat_btn = self.flip_btn.move(-60, 0)
        # the right sidebar is reserved whenever either pane wants it: the
        # move history, or the docked chat (which shrinks the history to fit)
        want_panel = self.settings["move_panel"] or self.chat_open
        self.panel_w = max(150, min(240, w // 3)) if want_panel else 0

        region_w = w - self.panel_w
        # //9 so there is always one extra square-width of space right of the
        # board, reserved for the turn-indicator pawns
        self.square = max(16, min((region_w - 24) // 9,
                                  (h - self.status_h - self.bottom_h - 24) // 8))
        self.margin = max(4, self.square // 10)
        self.board_px = self.square * 8
        self.pawn_size = int(self.square * 0.7)
        strip_w = self.square
        group_w = self.board_px + self.margin * 2 + strip_w
        self.board_off_x = max(0, (region_w - group_w) // 2)
        self.pawn_x = (self.board_off_x + self.margin * 2 + self.board_px
                       + (strip_w - self.pawn_size) // 2)
        self.panel_x = w - self.panel_w

        # move-review scrubber: a small strip just under the board, in the
        # slack the square sizing leaves above the bottom status bar
        review_y = self.status_h + self.board_px + self.margin * 2 + 3
        self.review_h = max(18, min(22, self.square // 2))
        self.prev_btn = pygame.Rect(self.board_off_x + self.margin,
                                    review_y, 46, self.review_h)
        self.next_btn = self.prev_btn.move(50, 0)
        self.live_btn = self.next_btn.move(50, 0).inflate(14, 0)

        # XP controls keep a fixed, crisp Tahoma size no matter the window;
        # only board labels, sprites and squares scale
        self.font = pygame.font.SysFont(UI_FONT, 12)
        self.panel_font = pygame.font.SysFont(UI_FONT, 11)
        self.small_font = pygame.font.SysFont(UI_FONT, 11)
        self.tiny_font = pygame.font.SysFont(UI_FONT, 9)
        self.big_font = pygame.font.SysFont(UI_FONT, 15, bold=True)
        self.title_font = pygame.font.SysFont(UI_FONT, 18, bold=True)
        # a fixed-width face for the Bot Dev code panel, so source aligns
        self.mono_font = pygame.font.SysFont("consolas,menlo,monospace", 11)
        self.sprites = self._scale_sprites(int(self.square * 0.9))
        self.minis = self._scale_sprites(max(12, int(self.square * 0.34)))
        self.app_icon = pygame.transform.scale(self.logo, (16, 16))
        self.board_surface = self._build_board_surface()
        # cross-board geometry for the four-player game: a 14x14 bounding grid
        # sized to the same region, with one spare column for the turn pawns
        self.csquare = max(10, min((region_w - 24) // (_CROSS_SIZE + 1),
                                   (h - self.status_h - self.bottom_h - 24)
                                   // _CROSS_SIZE))
        self.cross_px = self.csquare * _CROSS_SIZE
        self.cross_margin = max(3, self.csquare // 10)
        _cg = self.cross_px + self.cross_margin * 2
        self.cross_off_x = max(0, (region_w - _cg) // 2)
        self.cross_off_y = self.status_h + self.cross_margin
        self.sprites4 = self._scale_sprites4(int(self.csquare * 0.9))
        # tiny cross sprites for the move-history capture icons (a cross code
        # like "Pb" has no place in the two-army `minis` map)
        self.minis4 = self._scale_sprites4(max(12, int(self.csquare * 0.34)))
        self._cross_surface = self._build_cross_surface()

    def _scale_sprites(self, size):
        sprites = {}
        for row, color in ((0, "b"), (1, WHITE)):
            for name, col in _SPRITE_COLS.items():
                char = name if color == "b" else name.upper()
                frame = self.sheet.subsurface(col * SPRITE, row * SPRITE,
                                              SPRITE, SPRITE)
                sprites[char] = pygame.transform.scale(frame, (size, size))
        return sprites

    def _build_board_surface(self):
        """Pre-render the checkerboard and optional coordinates, flat and
        square like a classic bitmap control - the sunken 3D edge is drawn
        around it by draw_game."""
        light, dark = THEMES[self.settings["board_theme"]]
        sq, margin = self.square, self.margin
        size = (self.board_px + margin * 2, self.board_px + margin * 2)
        surf = pygame.Surface(size)
        surf.fill(xp.DK_SHADOW)
        for rank in range(8):
            for file_ in range(8):
                color = light if (rank + file_) % 2 == 0 else dark
                x = margin + file_ * sq
                y = margin + (7 - rank) * sq
                pygame.draw.rect(surf, color, (x, y, sq, sq))
        if self.settings["coordinates"]:
            # when flipped, the files run h..a left-to-right and the ranks
            # read 1..8 top-to-bottom; the checkerboard parity is unchanged
            # by a 180 degree rotation, so only the labels move
            for j in range(8):
                file_i = 7 - j if self.flipped else j
                rank_i = j if self.flipped else 7 - j
                file_lbl = self.tiny_font.render("abcdefgh"[file_i], True,
                                                 (240, 238, 228))
                rank_lbl = self.tiny_font.render(str(rank_i + 1), True,
                                                 (240, 238, 228))
                fx = margin + j * sq + sq // 2 - file_lbl.get_width() // 2
                surf.blit(file_lbl, (fx, margin + 1))
                ry = margin + j * sq + sq // 2 - rank_lbl.get_height() // 2
                surf.blit(rank_lbl, (margin + 2, ry))
        return surf

    # ---------------------------------------------------- cross-board (M8)

    def _scale_sprites4(self, size):
        """Scale every army-piece sprite the cross can show, keyed by its
        board4 code ("Pw", "Kbl"). White and black come off pieces.png (rows
        1 and 0); red and blue come off pieces2.png (rows 0 and 1), all in the
        shared K Q B N R P column order."""
        sprites = {}
        rows = {"w": (self.sheet, 1), "b": (self.sheet, 0),
                "r": (self.sheet2, 0), "bl": (self.sheet2, 1)}
        for army, (sheet, row) in rows.items():
            for name, col in _SPRITE_COLS.items():
                frame = sheet.subsurface(col * SPRITE, row * SPRITE,
                                         SPRITE, SPRITE)
                sprites[name.upper() + army] = pygame.transform.scale(
                    frame, (size, size))
        return sprites

    def _build_cross_surface(self):
        """Pre-render the cross checkerboard: the central 8x8 plus the four
        arms, with the 3x3 corner blocks left as the surrounding face."""
        light, dark = THEMES[self.settings["board_theme"]]
        sq, margin = self.csquare, self.cross_margin
        size = (self.cross_px + margin * 2, self.cross_px + margin * 2)
        surf = pygame.Surface(size)
        surf.fill(xp.DK_SHADOW)
        for y in range(_CROSS_SIZE):
            for x in range(_CROSS_SIZE):
                if not _cross_playable((x, y)):
                    continue
                color = light if (x + y) % 2 == 0 else dark
                # y = 0 is the north edge, so the top of the surface is row 0
                pygame.draw.rect(surf, color,
                                 (margin + x * sq, margin + y * sq, sq, sq))
        return surf

    def cross_to_px(self, sq):
        """Top-left pixel of a cross square (x, y); y grows downward from the
        north edge, matching the surface built by _build_cross_surface."""
        x, y = sq
        return (self.cross_off_x + self.cross_margin + x * self.csquare,
                self.cross_margin + y * self.csquare)

    def px_to_cross(self, pos):
        """The cross square under a window point, or None if it lands off the
        board or on one of the cut-out corners."""
        x = pos[0] - self.cross_off_x - self.cross_margin
        y = pos[1] - self.cross_margin - self.status_h
        if not (0 <= x < self.cross_px and 0 <= y < self.cross_px):
            return None
        sq = (x // self.csquare, y // self.csquare)
        return sq if _cross_playable(sq) else None

    def _cross_snapshot(self, board=None):
        """The cross position as _CROSS_SIZE rows of _CROSS_SIZE cells, north
        edge first. Empty squares are '.', the cut-out corners CROSS_OFF, and
        every piece keeps its full board4 code (kind + army) so the web page
        can pick the right pallet."""
        board = board if board is not None else self.game.board
        off = const.CROSS_OFF
        rows = []
        for y in range(_CROSS_SIZE):
            line = []
            for x in range(_CROSS_SIZE):
                if not _cross_playable((x, y)):
                    line.append(off)
                else:
                    line.append(board.get((x, y)) or ".")
            rows.append(line)
        return rows

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self._maximized = False
        self._drag = None
        self._win_pending = None
        self._applied_pos = self._applied_size = None
        self.settings["fullscreen"] = self.fullscreen
        self.settings.save()
        self.window = self._current_display_size() if self.fullscreen else DEFAULT_WINDOW
        self.screen = self._set_mode()
        self._layout()

    # ----------------------------------------------------------- coordinates

    def square_to_px(self, sq):
        """Top-left pixel of a board square, honouring the flip setting."""
        f, r = sq
        if self.flipped:
            f, r = 7 - f, 7 - r
        return (self.board_off_x + self.margin + f * self.square,
                self.margin + (7 - r) * self.square)

    def px_to_square(self, pos):
        x = pos[0] - self.board_off_x - self.margin
        y = pos[1] - self.margin - self.status_h
        if not (0 <= x < self.board_px and 0 <= y < self.board_px):
            return None
        f, r = x // self.square, 7 - y // self.square
        if self.flipped:
            f, r = 7 - f, 7 - r
        return f, r

    def toggle_flip(self):
        """Rotate the board 180 degrees; only the labels and mapping change."""
        self.flipped = not self.flipped
        self.selected = None
        self.selected_moves = []
        self.board_surface = self._build_board_surface()

    # ------------------------------------------------------------ game state

    def start_game(self):
        """New game with the players and the rules set up on the wizard."""
        if self.four:
            # a cross game re-opens through its own driver, keeping the seats
            # already chosen when there are any (a hosted game's remote seat)
            if self.seats4:
                self.start_game4(self.seats4, self._viewer or _A_WHITE)
            else:
                self.start_game4()
            return
        self.game = ChessGame()
        self.variant = variants.create(self.settings["variant"])
        self.variant.replay([])
        self.bot = self._make_bot("b")
        self.white_bot = self._make_bot("w")
        budget = getattr(self.args, "budget", None)
        for bot in (self.bot, self.white_bot):
            if bot is not None and budget:
                bot.budget = budget      # duck-typed: seconds per move
        self._think = None
        self._end_t = 0.0
        self._reported_ply = -1
        self.history = []
        self._review = [{"rows": self._rows_snapshot(), "last": []}]  # start pos
        self.view_ply = None
        self.scroll_y = 0
        self.follow_latest = True
        self.selected = None
        self.selected_moves = []
        self.last_move = None
        self.pending_promotion = None
        self.anim = None
        self.effects = []
        self._host_events = []
        self.show_over = False
        self.duel_ended = False
        self.open_menu = None
        self.show_about = False
        self.message = None
        self.message_pos = None
        self._msg_close = self._msg_cap = None
        self.over_pos = None
        self.over_dismissed = False
        self.about_pos = None
        self._drag = None
        self._win_pending = None
        self._applied_pos = self._applied_size = None
        self._refresh_fog()
        self._review[0]["fog"], self._review[0]["tint"] = self._view_fog_tint()
        self.state = GAME

    def _play(self, move):
        """Apply a move: record notation, start the slide, prep capture data."""
        board = self.game.board
        mover = board.get(move.from_sq)
        kind = mover.upper()
        notation = san(board, move)

        captured, cap_sq = board.get(move.to_sq), move.to_sq
        if kind == "P" and captured is None and move.to_sq == board.ep_target:
            cap_sq = (move.to_sq[0], move.from_sq[1])      # en passant victim
            captured = board.get(cap_sq)

        tracks = [(mover, move.from_sq, move.to_sq)]
        if kind == "K" and abs(move.to_sq[0] - move.from_sq[0]) == 2:
            rank = move.from_sq[1]
            rook = "R" if _piece_color(mover) == WHITE else "r"
            if move.to_sq[0] == 6:                          # kingside
                tracks.append((rook, (7, rank), (5, rank)))
            else:                                           # queenside
                tracks.append((rook, (0, rank), (3, rank)))

        self.game.play_move(move)
        blasts = self.variant.after_move(self.game.board, move)
        self._refresh_fog()
        self.history.append({"san": notation, "captured": captured,
                             "mover": _piece_color(mover),
                             "blast": blasts[0][1] if blasts else None,
                             "check": notation.endswith(("+", "#"))})
        self.follow_latest = True
        # record this position for the move scrubber and jump back to live
        fog, tint = self._view_fog_tint()
        self._review.append({"rows": self._rows_snapshot(),
                             "last": [sq_name(move.from_sq),
                                      sq_name(move.to_sq)],
                             "fog": fog, "tint": tint})
        self.view_ply = None
        self.last_move = move
        self.selected = None
        self.selected_moves = []
        self.anim = {"tracks": tracks, "t": 0.0,
                     "dur": self.settings.anim_seconds,
                     "captured": (captured, cap_sq) if captured else None,
                     "blast": blasts}
        # one sound per move, the loudest thing it did being the one you hear
        self.sfx.play("blast" if blasts else "capture" if captured
                      else "check" if notation.endswith(("+", "#")) else "move")
        if self.hosting:
            # journal the move for the guest page to animate; the id keeps
            # climbing even when the journal empties, so a client that has
            # seen a 12 never replays an older one
            self._host_ev_seq += 1
            self._host_events.append({
                "id": self._host_ev_seq,
                "tracks": [[p, sq_name(a), sq_name(b)]
                           for p, a, b in tracks],
                "capture": [sq_name(cap_sq), captured] if captured else None,
                "blast": [sq_name(b_sq) for b_sq, _p in blasts],
            })
            del self._host_events[:-8]
            self.host.publish(self._host_snapshot())   # show it right away

    def _variant_redo(self):
        """A replayer for an undo rebuild: a fresh variant laid from the same
        seed, which detonates each mine again as the move that trod it is
        replayed. The live variant cannot do this - by now it has spent the
        mines, so a rebuild leaning on it would leave the blasted pieces
        standing and the later moves illegal."""
        return variants.create(self.variant.key, self.variant.seed).after_move

    def _undo(self):
        if self.four:
            return self._undo4()
        self._think = None
        if not self.game.moves:
            return
        redo = self._variant_redo()
        self.game.undo(redo)
        self.history.pop()
        # versus a bot, roll back the bot's reply too so the human is to move;
        # each rebuild starts from the opening again, so each wants its own
        # freshly laid field
        while (not self.duel() and self.game.moves
               and self._side_bot(self.game.board.turn)):
            self.game.undo(redo)
            self.history.pop()
        # mines are re-laid from the game's seed and re-detonated ply by ply,
        # so an undo lands on exactly the minefield that position came from
        self.variant.replay(self.game.moves)
        self._refresh_fog()
        self.selected = None
        self.selected_moves = []
        self.show_over = False
        self.over_dismissed = False
        self.anim = None
        self.effects = []
        self._host_events = []      # rolled-back moves must not replay on page
        self.last_move = self.game.moves[-1] if self.game.moves else None
        # the review history shrinks with the game: keep one entry per ply
        # plus the start position, and follow live again
        self._review = self._review[:len(self.history) + 1]
        self.view_ply = None

    # ----------------------------------------------------------- move review

    def _rows_snapshot(self, board=None):
        """The position as eight strings, rank 8 first, '.' for empty - the
        same shape the web page and a review entry both draw from."""
        board = board if board is not None else self.game.board
        return ["".join(board.get((f, rank)) or "." for f in range(8))
                for rank in range(7, -1, -1)]

    # ================================================== four-player game (M8)
    # A dedicated driver for the cross board. It owns its own seats (self.seats4,
    # keyed by army id), its own move/click/update path and its own renderer, so
    # the two-player ChessGame path above is never reached while self.four is on.
    # Armies whose seat is AI move on their gate-allowed engine; a HUMAN seat
    # moves on clicks; a REMOTE seat (a hosted guest) moves on the server's
    # queue.

    def start_game4(self, seats=None, viewer=_A_WHITE):
        """Open a four-army cross game. `seats` maps each army id to a seat
        ({"type": HUMAN/AI/REMOTE, "name": ...}); the default is one local
        human (white) against three engines."""
        self.four = True
        self.game = Game4()
        self.variant = variants.create(self.settings["variant"], four=True)
        self.variant.replay([])
        if seats is None:
            engine = self._default_engine(self._mode_token(4, "offline"))
            seats = {"w": {"type": HUMAN, "name": "You"},
                     "b": {"type": AI, "name": "Computer", "bot": engine},
                     "r": {"type": AI, "name": "Computer", "bot": engine},
                     "bl": {"type": AI, "name": "Computer", "bot": engine}}
        self.seats4 = seats
        self._viewer = viewer
        # every AI army needs an engine that plays this variant: there is no
        # built-in mover any more, and no two-player bot instance is left
        # hanging (seating guarantees the engine; a mode with none refuses to
        # start rather than fall back to one that cannot read the cross board)
        self.bot = self.white_bot = None
        self._think = None
        self._think4 = None
        self._end_t = 0.0
        self._reported_ply = -1
        self.history = []
        self._review = [{"cells": self._cross_snapshot(), "last": []}]
        self.view_ply = None
        self.scroll_y = 0
        self.follow_latest = True
        self.selected = None
        self.selected_moves = []
        self.last_move = None
        self.pending_promotion = None
        self.anim = None
        self.effects = []
        self._host_events = []
        self.show_over = False
        self.over_dismissed = False
        self._ai_t = 0.0
        self._refresh_fog4()
        self._review[0]["fog"], self._review[0]["tint"] = self._view_fog_tint()
        self.state = GAME

    def _refresh_fog4(self):
        """Which cross squares the current viewer may not see. A seated army
        sees only its own blind spots; with nobody seated (an all-engine game
        or a spectator) the screen shows the union of every standing army's
        sight - fog over the tiles nobody can see, tinted by who can."""
        if self._viewer:
            self._fog = self.variant.hidden(self.game.board, self._viewer)
            self._tint = {}
        else:
            self._fog, self._tint = self.variant.visibility(self.game.board)

    def _play4(self, move):
        """Play one cross move: record notation, re-derive fog/mines, journal
        it for any hosted page, and publish the fresh position."""
        board = self.game.board
        mover = board.get(move.from_sq)
        notation = _san4(board, move)
        captured = board.get(move.to_sq)
        self.game.play_move(move)
        blasts = self.variant.after_move(board, move)
        self._refresh_fog4()
        self.history.append({"san": notation, "captured": captured,
                             "mover": _cross_army(mover),
                             "blast": blasts[0][1] if blasts else None,
                             "check": notation.endswith(("+", "#"))})
        self.follow_latest = True
        fog, tint = self._view_fog_tint()
        self._review.append({"cells": self._cross_snapshot(),
                             "last": [move.from_sq, move.to_sq],
                             "fog": fog, "tint": tint})
        self.view_ply = None
        self.last_move = move
        self.selected = None
        self.selected_moves = []
        self.sfx.play("capture" if captured else "check"
                      if notation.endswith(("+", "#")) else "move")
        if self.hosting and self.host is not None:
            self._host_ev_seq += 1
            self._host_events.append({
                "id": self._host_ev_seq,
                "from": _cross_sq_name(move.from_sq),
                "to": _cross_sq_name(move.to_sq),
                "capture": bool(captured)})
            del self._host_events[:-8]
            self.host.publish(self._host_snapshot())

    def _update4(self, dt):
        """Advance the cross game one frame: relay the hosted lobby, then hand
        a turn to an engine, the guest's queued move, or a click."""
        self.glow_t += dt
        self.effects = [(e, x, y) for (e, x, y) in self.effects if not e.done]
        for effect, _, _ in self.effects:
            effect.update(dt)
        # post the live report and quit on the finished result every frame,
        # ahead of the early returns below, so a decided cross game still gets
        # its last PGN out and its --report shutdown
        self._report_and_exit(dt)
        if self.hosting and self.host is not None:
            self._host_pub_t += dt
            if self._host_pub_t >= 0.25:
                self._host_pub_t = 0.0
                self.host.publish(self._host_snapshot())
            for message in self.host.take_chat():
                self.note_chat(message)
            gave_up = self.host.take_resign()
            if gave_up is not None and not self.game.result():
                # which army gave up comes from the seat it was played from
                self.game.resign(gave_up or self.guest_army)
                self._refresh_fog4()
        if self.lan_client:
            # a joining app runs no engine and owns no server: the poll
            # rebuilds the position, clicks post moves over the wire, and
            # the end dialog follows whatever the host's board ends as
            self._poll_client(dt)
            if self.game.result() and not self.show_over \
                    and not self.over_dismissed:
                self.show_over = True
                self.sfx.play("end")
            return
        if self.game.result():
            if not self.show_over and not self.over_dismissed:
                self.show_over = True
                self.sfx.play("end")
            return
        if self.reviewing():
            return
        army = self.game.board.turn
        seat = self.seats4.get(army, {"type": AI})
        if seat["type"] == REMOTE:
            asked = self.host.take_move(army) if self.host is not None else None
            if asked:
                self._play_guest_move4(asked)
                return
            if self.host is not None and not self._remote_held(army):
                # nobody holds this seat right now - never claimed, or its
                # player left or went quiet - so cover it with an engine that
                # sees only what this army may know; with none for the mode
                # the seat waits and the reason shows, never a cheat-mover
                engine = self._default_engine(self._mode_token(4))
                if engine is None:
                    self.message = (const.NO_ENGINE_TITLE, const.NO_ENGINE_MSG)
                    return
                if self._bot_update4(army, {"type": AI, "name": engine,
                                            "bot": engine}, dt):
                    return
                self._ai_t += dt
                if self._ai_t < self.think_delay:
                    return
                self._ai_t = 0.0
                move = self._closest(self.game.board.legal_moves(), None)
                if move is not None:
                    self._play4(move)
            return
        if seat["type"] != AI:
            return                       # a local human's turn: wait for clicks
        if seat.get("bot") and self._bot_update4(army, seat, dt):
            return              # an engine owns this turn: it is thinking, or
            # has its move and is holding it for the think delay
        # An AI seat no engine can drive: seating guarantees an allowed engine,
        # so this is reached only when that engine momentarily fails to start.
        # Play a random legal move so the game cannot stall - never a mover
        # that would see straight through the fog.
        self._ai_t += dt
        if self._ai_t < self.think_delay:
            return
        self._ai_t = 0.0
        move = self._closest(self.game.board.legal_moves(), None)
        if move is not None:
            self._play4(move)

    def _make_bot4(self, name):
        """Fresh instance of a cross engine, set the way that bot's saved
        options are; None when it is not installed or will not start."""
        cls = self.bots.get(name)
        if cls is None:
            return None
        try:
            bot = cls()
            bot.configure(self.settings.bot_options(
                name, getattr(cls, "CONFIG", {}) or {}))
        except Exception as exc:  # noqa: BLE001 - a broken bot must not crash the game
            print(f"{name} could not start: {exc}")
            return None
        budget = getattr(self.args, "budget", None)
        if budget:
            bot.budget = budget         # duck-typed: --budget every cross seat
        return bot

    def _bot_update4(self, army, seat, dt):
        """Drive the engine seated on one cross army. True while it owns the
        turn - thinking, or holding the move it found for the think delay -
        and False when it cannot start at all, so the caller covers the army
        with a random legal move. The engine works in its own thread, exactly
        as a two-player bot does, so a deep search never stalls the frames, and
        anything it says through `position.info.say()` goes into the chat."""
        ply = len(self.game.moves)
        think = self._think4
        if think is not None and (think["army"] != army
                                  or think["ply"] != ply):
            # the board moved underneath it (undo, a restart, an opponent's
            # edit): whatever the worker returns is stale, so start over
            think = self._think4 = None
        if think is None:
            bot = self._make_bot4(seat["bot"])
            if bot is None:
                return False
            count, net, variant = self._mode_parts(4)
            info = GameInfo(count, variant, net, army)
            box = {"move": None, "done": False}
            # the variant decides what the engine may know, as it decides what
            # the human on that army is shown
            view = self.variant.bot_view(self.game.board)
            # on the true board (classic) hand the engine its own repetition
            # history, so it can refuse a shuffle rather than walk into the
            # cross game's fourth-time-loss; under fog/mine the counts do not
            # line up, so the progress filter is left to sleep
            if variant == "classic":
                info.seen = self.game.repetitions.get(_sig4(view), 0)
                info.recent = frozenset(self.game.recent)
            position = Position(view, info=info)

            def work():
                try:
                    box["move"] = bot.choose(position)
                except Exception as exc:  # noqa: BLE001 - never crash the game
                    print(f"{bot.name} failed to pick a move: {exc}")
                finally:
                    box["done"] = True

            threading.Thread(target=work, daemon=True).start()
            self._think4 = {"army": army, "side": army, "ply": ply, "t": 0.0,
                            "box": box, "info": info, "name": bot.name}
            return True
        think["t"] += dt
        box = think["box"]
        self._say_chat(think)
        if not box["done"] or think["t"] < self.think_delay:
            return True
        move = box["move"]
        think, self._think4 = think, None
        self._say_chat(think)
        legal = self.game.board.legal_moves()
        if not legal or move is None:
            return False       # nothing usable: let the caller cover it
        # sanity-check the engine like any other player: an engine thinking
        # under fog can aim at a square the real board forbids
        if move not in legal:
            move = self._closest(legal, move)
        self._play4(move)
        return True

    def _play_guest_move4(self, asked):
        """Turn a hosted guest's requested move (cross coordinate text) into a
        legal cross move for its army and play it."""
        frm = _parse_cross(asked.get("from"))
        to = _parse_cross(asked.get("to"))
        if frm is None or to is None:
            return
        army = self.game.board.turn
        legal = [m for m in self.game.board.legal_moves()
                 if m.from_sq == frm and m.to_sq == to]
        if not legal:
            return
        want = asked.get("promotion") or "q"
        move = next((m for m in legal if m.promotion == want), legal[0])
        self._play4(move)

    def handle_game4_click(self, pos):
        """Select and move pieces for a human-held army on the cross."""
        if self.reviewing() or self.game.result():
            return
        army = self.game.board.turn
        seat = self.seats4.get(army)
        if not seat or seat["type"] != HUMAN:
            return                       # engines and guests move themselves
        sq = self.px_to_cross(pos)
        if sq is None:
            return
        if self.selected:
            moves = [m for m in self.selected_moves if m.to_sq == sq]
            if moves:
                promo = [m for m in moves if m.promotion == "q"]
                self._play4(promo[0] if promo else moves[0])
                return
        piece = self.game.board.get(sq)
        if piece and _cross_army(piece) == army:
            self.selected = sq
            self.selected_moves = [m for m in self.game.board.legal_moves()
                                   if m.from_sq == sq]
            self.sfx.play("select")
        else:
            self.selected = None
            self.selected_moves = []

    def _undo4(self):
        """Roll the cross game back one ply by replaying from the start, so the
        elimination sweep and any minefield run again in the same order."""
        self._think = None
        self._think4 = None
        if not self.game.moves:
            return
        redo = variants.create(self.variant.key, self.variant.seed,
                               four=True).after_move
        self.game.undo(redo)
        self.variant.replay(self.game.moves)
        self.history.pop()
        self._review = self._review[:len(self.history) + 1]
        self._refresh_fog4()
        self.selected = None
        self.selected_moves = []
        self.show_over = False
        self.over_dismissed = False
        self.anim = None
        self.effects = []
        self._host_events = []
        self.last_move = self.game.moves[-1] if self.game.moves else None
        self.view_ply = None

    def _draw_cross_cells(self, cells, fog=frozenset()):
        """Blit the pieces of a stored cross snapshot (used while reviewing),
        skipping any square that ply's own fog hides."""
        sq = self.csquare
        for y, row in enumerate(cells):
            for x, code in enumerate(row):
                if (code in (".", const.CROSS_OFF)
                        or not _cross_playable((x, y)) or (x, y) in fog):
                    continue
                sprite = self.sprites4.get(code)
                if sprite is None:
                    continue
                px, py = self.cross_to_px((x, y))
                off = (sq - sprite.get_width()) // 2
                self.screen.blit(sprite, (px + off, py + self.status_h + off))

    def draw_game4(self):
        """Paint the cross board, its pieces, highlights and the four turn chips."""
        w, h = self.window
        sq, status_h = self.csquare, self.status_h
        self.screen.fill(COL_BG, (0, self.chrome_top, w,
                                  max(1, h - self.chrome_top - self.bottom_h)))
        self.screen.blit(self._cross_surface, (self.cross_off_x, status_h))
        frame = self._cross_surface.get_rect()
        frame.topleft = (self.cross_off_x, status_h)
        xp.sunken(self.screen, frame.inflate(4, 4).move(-2, -2))
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        board = self.game.board
        if self.reviewing():
            # a past cross position wears its own fog and team tint
            entry = self._review[self.view_ply]
            fog = self._review_fog(entry, _parse_cross)
            tint = self._review_tint(entry, _parse_cross)
            for fog_sq in fog:
                x, y = self.cross_to_px(fog_sq)
                pygame.draw.rect(overlay, COL_FOG, (x, y + status_h, sq, sq))
            self._paint_tint(overlay, tint, self.cross_to_px, sq, status_h)
            for mv in (entry.get("last") or []):
                x, y = self.cross_to_px(mv)
                pygame.draw.rect(overlay, COL_LAST_MOVE,
                                 (x, y + status_h, sq, sq))
            self.screen.blit(overlay, (0, 0))
            self._draw_cross_cells(entry["cells"], fog)
            self.draw_status()
            self.draw_bottom_hint()
            self.draw_turn_indicators4()
            return
        for fog_sq in self._fog:
            x, y = self.cross_to_px(fog_sq)
            pygame.draw.rect(overlay, COL_FOG, (x, y + status_h, sq, sq))
        self._paint_tint(overlay, self._tint, self.cross_to_px, sq, status_h)
        if self.last_move:
            for mv in (self.last_move.from_sq, self.last_move.to_sq):
                if mv in self._fog:
                    continue
                x, y = self.cross_to_px(mv)
                pygame.draw.rect(overlay, COL_LAST_MOVE,
                                 (x, y + status_h, sq, sq))
        if self.selected and self.selected not in self._fog:
            x, y = self.cross_to_px(self.selected)
            pygame.draw.rect(overlay, COL_SELECTED, (x, y + status_h, sq, sq))
        turn = board.turn
        if turn is not None and board.in_check(turn):
            king_sq = board.find_king(turn)
            if king_sq:
                x, y = self.cross_to_px(king_sq)
                pygame.draw.rect(overlay, COL_CHECK, (x, y + status_h, sq, sq))
        self.screen.blit(overlay, (0, 0))
        for y in range(_CROSS_SIZE):
            for x in range(_CROSS_SIZE):
                if not _cross_playable((x, y)) or (x, y) in self._fog:
                    continue
                code = board.get((x, y))
                if not code:
                    continue
                sprite = self.sprites4.get(code)
                if sprite is None:
                    continue
                px, py = self.cross_to_px((x, y))
                off = (sq - sprite.get_width()) // 2
                self.screen.blit(sprite, (px + off, py + status_h + off))
        if self.settings["move_hints"]:
            for move in self.selected_moves:
                x, y = self.cross_to_px(move.to_sq)
                cx, cy = x + sq // 2, y + status_h + sq // 2
                if board.get(move.to_sq):
                    pygame.draw.circle(self.screen, COL_DOT[:3], (cx, cy),
                                       sq // 2 - 3, width=max(2, sq // 12))
                else:
                    pygame.draw.circle(self.screen, COL_DOT[:3], (cx, cy),
                                       max(3, sq // 7))
        self.draw_status()
        self.draw_review_strip()
        self.draw_bottom_hint()
        self.draw_turn_indicators4()
        if self.settings["move_panel"] or self.chat_open:
            self.draw_panel()
        if self.show_over:
            self.draw_game_over()

    def draw_turn_indicators4(self):
        """Four army chips beside the cross board; the standing army whose turn
        it is pulses gold, and one already eliminated is dimmed."""
        x = self.cross_off_x + self.cross_margin + self.cross_px + 8
        cy = self.status_h + self.cross_margin + 6
        turn = self.game.board.turn
        wave = 0.5 + 0.5 * math.sin(self.glow_t * math.tau / 1.6 - math.pi / 2)
        side = max(10, self.csquare)
        for army in _ARMY_IDS:
            out = army in self.game.board.out
            base = const.ARMY_TINT[army]
            shade = tuple(int(c * (0.4 if out else 1.0)) for c in base)
            rect = pygame.Rect(x, cy, side, side)
            pygame.draw.rect(self.screen, shade, rect)
            pygame.draw.rect(self.screen, COL_TEXT, rect, 1)
            if army == turn and not self.game.result():
                pygame.draw.rect(self.screen, COL_GOLD, rect.inflate(4, 4),
                                 max(2, int(3 * wave)))
            cy += side + 4

    def review_available(self):
        """Is there more than the start position to scrub through?"""
        return len(self._review) > 1

    def review_live(self):
        return len(self._review) - 1

    def reviewing(self):
        """True only while parked on an earlier ply; at/after the latest the
        board is live and clickable again."""
        return self.view_ply is not None and self.view_ply < self.review_live()

    def review_prev(self):
        n = self.review_live()
        if n < 1:
            return
        cur = n if self.view_ply is None else self.view_ply
        self.view_ply = max(0, cur - 1)

    def review_next(self):
        if self.view_ply is None:
            return
        self.view_ply += 1
        if self.view_ply >= self.review_live():
            self.view_ply = None    # caught up: follow live again

    def review_jump_live(self):
        self.view_ply = None

    # ------------------------------------------------------------ main loop

    def run(self):
        running = True
        while running:
            dt = self.clock_tick()
            running = self.pump_events()
            # geometry changes must happen *outside* the event pump: moving
            # a borderless window dispatches WM_MOVE and synthetic mouse
            # messages, and applying them while iterating pygame's events
            # re-enters the message loop until the stack overflows
            self._apply_window_drag()
            self.update(dt)
            self.draw()
            pygame.display.flip()
        pygame.quit()

    def _apply_window_drag(self):
        """Move/resize the SDL window once per frame, after the pump."""
        p = self._win_pending
        if not p:
            return
        pos, size = p.get("pos"), p.get("size")
        if pos == self._applied_pos and (size is None or size == self._applied_size):
            self._win_pending = None   # moving the window under a still
            return                     # cursor re-triggers motions: dedupe
        self._win_pending = None
        win = self._sdlwin()
        if win is None:
            return
        try:
            if size:
                win.size = size
            if pos:
                win.position = pos
            self._applied_pos, self._applied_size = pos, size
        except Exception:
            self._drag = None
            return
        # relative-motion fallback (no global mouse): the anchors follow
        # whatever was actually applied, so the window's own movement never
        # feeds back into the next in-window cursor reading
        d = self._drag
        if d is not None and d.get("g0") is None:
            if pos:
                d["base"] = pos
            if size:
                d["bsize"] = size
            d["m_anchor"] = pygame.mouse.get_pos()

    def pump_events(self):
        """Dispatch all queued events; returns False when the app should quit."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            elif event.type == pygame.VIDEORESIZE:
                self.window = (max(event.w, MIN_WINDOW[0]),
                               max(event.h, MIN_WINDOW[1]))
                self._layout()
            elif event.type == pygame.MOUSEWHEEL and self._browser is not None:
                self.handle_browser_event(event)
            elif event.type == pygame.MOUSEWHEEL and self.state == GAME:
                self.scroll_panel(event.y)
            elif event.type == pygame.MOUSEWHEEL and self.state == SETTINGS:
                self._settings_scroll -= event.y * 24
                self.clamp_settings_scroll()
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                self.toggle_fullscreen()
            else:
                self.handle_event(event)
        return True

    def clock_tick(self):
        if not hasattr(self, "clock"):
            self.clock = pygame.time.Clock()
        return self.clock.tick(60) / 1000.0

    def update(self, dt):
        self._poll_browser()
        self._poll_client_update()
        self._poll_update_probe()
        if self.hosting and self.state == MENU:
            self.stop_host()          # back at the menu: the server is done
        if self.lan_client and self.state == MENU:
            self._leave_client()      # back at the menu: hang up the connection
        if self.state == LANWAIT:
            self._lan_wait_tick(dt)   # lobby: publish, but nothing has started
            return
        if self.state != GAME:
            return
        if self.four:
            self._update4(dt)           # a cross game runs its own driver
            return
        self.glow_t += dt
        if self.anim:
            self.anim["t"] += dt
            if self.anim["t"] >= self.anim["dur"]:
                if self.settings["capture_effects"]:
                    captured = self.anim["captured"]
                    if captured:
                        piece, cap_sq = captured
                        x, y = self.square_to_px(cap_sq)
                        effect = make_capture_effect(piece, self.sprites[piece])
                        self.effects.append((effect, x, y + self.status_h))
                    # anything that trod a mine goes up as well
                    for b_sq, piece in self.anim.get("blast") or []:
                        x, y = self.square_to_px(b_sq)
                        self.effects.append((make_blast_effect(
                            self.sprites[piece]), x, y + self.status_h))
                self.anim = None
                if self.game.result() and not self.over_dismissed:
                    self.show_over = True
                    self.sfx.play("end")
        self.effects = [(e, x, y) for (e, x, y) in self.effects if not e.done]
        for effect, _, _ in self.effects:
            effect.update(dt)
        if (self._side_bot(self.game.board.turn) and not self.anim
                and not self.pending_promotion and not self.show_over
                and not self.duel_ended):
            self._bot_update(dt)
        if self.hosting and self.host is not None:
            self._host_tick(dt)
        if self.lan_client:
            self._poll_client(dt)
        self._report_and_exit(dt)

    # ------------------------------------------------------------ player seats
    # Who plays each side lives in self.players: one seat per colour, either a
    # human or a named engine carrying its own option values. Two seats on the
    # same engine can be set to different difficulties, which a single shared
    # "opponent" setting could never say.

    def seat(self, color):
        """The seat a side of the board is played from."""
        return self.players["w" if color == WHITE else "b"]

    def _seat_bot(self, seat):
        """The engine a seat names, or None while it is a human's seat."""
        name = self.players[seat]["bot"]
        if self.players[seat]["type"] != AI or name not in self.bots:
            return None
        return name

    def _set_seat_bot(self, seat, name):
        """Seat an engine: the side becomes a computer one and starts out with
        the options that engine was last played with."""
        s = self.players[seat]
        s["bot"] = name if name in self.bots else None
        s["type"] = AI if s["bot"] else HUMAN
        s["config"] = (dict(self.settings.bot_options(
            s["bot"], self.bots[s["bot"]].CONFIG)) if s["bot"] else {})

    def _set_seat_type(self, seat, player):
        """Make a seat human or computer; becoming a computer needs an engine,
        so with an empty bots/ folder that choice is simply not offered."""
        s = self.players[seat]
        if player == AI and not self.bots:
            return False
        s["type"] = player
        if player == AI and s["bot"] not in self.bots:
            self._set_seat_bot(seat, self._default_engine())
        elif player == HUMAN:
            self._text_focus = None      # nothing to configure any more
        return True

    def _swap_colors(self):
        """Trade the seats over: whoever was White takes Black, names and
        engine setups travelling with the side they were set up for."""
        self.players["w"], self.players["b"] = (self.players["b"],
                                                self.players["w"])
        self._drop = self._text_focus = None

    def _clean_players(self, players):
        """Seats as they can actually be played: an engine seat must name an
        installed bot, else it falls back to the first one, and where there is
        no engine at all the seat can only belong to a human. Each engine seat
        carries its own copy of that bot's CONFIG values, filled in from the
        values the bot was last used with."""
        out = {}
        for color, s in clean_players(players).items():
            if s["type"] == AI:
                if s["bot"] not in self.bots:
                    s["bot"] = self._default_engine()
                if s["bot"] is None:
                    s["type"], s["bot"] = HUMAN, None
            spec = self.bots[s["bot"]].CONFIG if s["bot"] in self.bots else {}
            saved = (self.settings.bot_options(s["bot"], spec) if spec else {})
            chosen = {k: v for k, v in s["config"].items()
                      if k in spec and v in spec[k]["choices"]}
            s["config"] = saved | chosen
            out[color] = s
        return out

    def _mode_key(self):
        """The arrangement the two seats add up to; the caption and the PGN
        header read it from here instead of from a stored mode."""
        return SEAT_MODE.get((self.players["w"]["type"],
                              self.players["b"]["type"]), "human_vs_ai")

    def _four_players(self):
        """Is the arrangement being edited or played a four-army cross one?
        True for a live cross game and for the setup pages that picked four
        armies, so mode tokens are right before a game exists. Every flag is
        read defensively so an early caller (validating the saved seats during
        startup) sees the two-player answer rather than an attribute that has
        not been set yet."""
        return bool(getattr(self, "four", False)
                    or getattr(self, "offline_players", 2) == 4
                    or getattr(self, "lan_players", 2) == 4)

    def _seat_duel(self):
        """True when no human holds a seat: every army (or both colours) is
        played by an engine, so the game is a duel the bots talk over."""
        if self._four_players():
            seats = list((getattr(self, "seats4", None)
                          or getattr(self, "offline_seats", None)
                          or {}).values())
            return bool(seats) and not any(s.get("type") == HUMAN
                                           for s in seats)
        return self.duel()

    def _net_kind(self):
        """"offline", "lan" (a hosted or joined game) or "duel" - a game no
        human plays. Read off the live flags, so it only answers for a page
        or a game that has finished opening."""
        if (getattr(self, "hosting", False) or getattr(self, "lan_client", False)
                or getattr(self, "lan_mode", False)):
            return "lan"
        return "duel" if self._seat_duel() else "offline"

    def _variant_key(self):
        """The rules key a mode token is built from: always the chosen variant
        in the settings. The rules page writes it the moment a ruleset is
        picked and start_game turns it into the live variant, so a seat gated
        on the setup pages - before the board has been rebuilt - is measured
        against the rules it will actually be played under, not a stale
        variant left over from the last game."""
        return self.settings.get("variant", "classic")

    def _mode_parts(self, players=None):
        """(army count, network, variant) of the game being set up or played."""
        if players is None:
            players = 4 if self._four_players() else 2
        return players, self._net_kind(), self._variant_key()

    def _mode_token(self, players=None, net=None):
        """The mode string engines declare themselves against:
        "<n>p/<net>/<variant>", matched by prefix, so a bot that only knows
        it plays two-player games lists ("2p",) and still matches every
        2p mode, whatever its variant or network. `net` is read from the live
        flags unless a caller states it, as the setup pages do."""
        if players is None:
            players = 4 if self._four_players() else 2
        return "%dp/%s/%s" % (players, net or self._net_kind(),
                              self._variant_key())

    def _bot_allows(self, name, mode=None):
        """Whether the installed bot `name` may sit a seat in this mode. A
        bot declares its modes in its class attribute MODES and may overrule
        the network half of it with LAN/DUEL; bots written before that API
        existed default to two-player. The browser's force-all-modes toggle
        overrides every declaration."""
        cls = self.bots.get(name)
        if cls is None:
            return False
        if self.settings.get("allow_all_modes"):
            return True
        return bot_allows(cls, mode or self._mode_token())

    def _bots_for_mode(self, mode=None):
        """Installed bot names that will play `mode`, in load order."""
        mode = mode or self._mode_token()
        return [name for name in self.bots if self._bot_allows(name, mode)]

    def _default_engine(self, mode=None):
        """The engine a computer seat falls back to when none is named: the
        first installed engine allowed to play `mode`, or None when nothing
        installed will. There is no built-in mover any more - with no engine
        for the mode a seat cannot be filled and the game refuses to start."""
        allowed = self._bots_for_mode(mode)
        return allowed[0] if allowed else None

    def player_name(self, color):
        """What a side is called: the name typed for it on the setup page,
        else its engine's name, else simply Human."""
        s = self.seat(color)
        return s["name"] or (s["bot"] if s["type"] == AI else "") or "Human"

    def _make_bot(self, seat):
        """Fresh instance of the engine sitting on a seat, configured the way
        that seat is set; None where the seat belongs to a human."""
        name = self._seat_bot(seat)
        if name is None:
            return None
        bot = self.bots[name]()
        bot.configure(self.players[seat]["config"])
        self.players[seat]["config"] = dict(bot.options)
        return bot

    def _side_bot(self, color):
        """The Bot playing `color`, or None where that side is a human."""
        if color == self.bot_color:
            return self.bot
        return self.white_bot

    def duel(self):
        """True when both sides are bots (AI vs AI mode). Read defensively so
        an early startup caller resolving a mode token never trips on bot
        seats that have not been created yet."""
        return (getattr(self, "bot", None) is not None
                and getattr(self, "white_bot", None) is not None)

    # ----------------------------------------------------------------- fog

    def _viewer_color(self):
        """Whose eyes the screen shows: the human's in a game against a bot,
        the side to move in a hot-seat game, and nobody in a duel - a
        spectator is shown the whole board."""
        if self.game is None or self.duel():
            return None
        humans = [c for c in SEATS if self.players[c]["type"] == HUMAN]
        if len(humans) == 1:      # one player against an engine
            return WHITE if humans[0] == "w" else BLACK
        return self.game.board.turn

    def _refresh_fog(self):
        """Recompute the fog for the current view. A seated viewer sees only
        its own army's blind spots; with nobody seated (a duel, an all-engine
        game) the screen shows the union of every army's sight - fog over the
        tiles nobody can see, and a per-tile tint naming who can."""
        self._viewer = self._viewer_color()
        if self._viewer:
            self._fog = self.variant.hidden(self.game.board, self._viewer)
            self._tint = {}
        else:
            self._fog, self._tint = self.variant.visibility(self.game.board)

    def _shown(self, sq):
        """Can the viewer see this square at all right now?"""
        return sq not in self._fog

    def _view_fog_tint(self):
        """The active view's fog and tint as JSON-safe square names: parked
        into every review entry so an earlier ply keeps its own fog (and team
        tint) instead of the live one, and so the server can mask each stored
        grid by it. A seated view has no tint; a union view tints each shown
        tile with the armies that can see it."""
        name = _cross_sq_name if self.four else sq_name
        return ([name(sq) for sq in self._fog],
                {name(sq): sorted(owners) for sq, owners in self._tint.items()})

    def _paint_tint(self, overlay, tint, to_px, sq, status_h):
        """Blend each shown tile's seeing-army tints onto the overlay - only
        a union view carries a tint, so a seated view paints nothing."""
        for tsq, owners in (tint or {}).items():
            x, y = to_px(tsq)
            pygame.draw.rect(overlay, _blend_tint(owners),
                             (x, y + status_h, sq, sq))

    @staticmethod
    def _review_fog(entry, parse):
        """A review ply's parked fog, back into board squares."""
        return frozenset(filter(None, (parse(n)
                                       for n in (entry.get("fog") or []))))

    @staticmethod
    def _review_tint(entry, parse):
        """A review ply's parked tint, keyed by board square."""
        out = {}
        for name, owners in (entry.get("tint") or {}).items():
            sq = parse(name)
            if sq:
                out[sq] = owners
        return out

    def _report_and_exit(self, dt):
        """--report FILE: keep the live PGN current while an AI vs AI game
        runs (the tournament polls it), then close on the finished result."""
        if not self.report_path or self.game is None:
            return
        if self.duel_ended:
            return          # an End Game stop is not a finished result to post
        if len(self.history) != self._reported_ply:
            try:
                pathlib.Path(self.report_path).write_text(
                    self.pgn_text(), encoding="utf-8")
                self._reported_ply = len(self.history)
            except OSError:
                pass
        if self.game.result():
            self._end_t += dt
            if self._end_t >= 4.0:
                self.report_path = None      # post the quit exactly once
                pygame.event.post(pygame.event.Event(pygame.QUIT))

    def _bot_update(self, dt):
        """Drive whichever bot is to move: think in a thread, play when done,
        and let anything it says through the chat channel reach the panel."""
        bot = self._side_bot(self.game.board.turn)
        if bot is None or self.game.result():
            return
        if self._think is None:
            box = {"move": None, "done": False}
            side = self.game.board.turn
            count, net, variant = self._mode_parts()
            info = GameInfo(count, variant, net, side)
            # the variant decides what the bot is allowed to know, exactly
            # like it decides what the human is shown
            position = Position(self.variant.bot_view(self.game.board),
                                info=info)

            def work():
                try:
                    box["move"] = bot.choose(position)
                except Exception as exc:  # noqa: BLE001 - a broken bot must not crash the game
                    print(f"{bot.name} failed to pick a move: {exc}")
                finally:
                    box["done"] = True

            threading.Thread(target=work, daemon=True).start()
            self._think = {"t": 0.0, "box": box, "info": info,
                           "name": bot.name, "side": side}
            return
        self._think["t"] += dt
        box = self._think["box"]
        self._say_chat(self._think)
        if box["done"] and self._think["t"] >= self.think_delay:
            move = box["move"]
            think, self._think = self._think, None
            self._say_chat(think)
            legal = self.game.board.legal_moves()
            if legal:
                # sanity-check the bot like any other player: a bot thinking
                # under fog can aim at a square the real board forbids
                if move not in legal:
                    move = self._closest(legal, move)
                self._play(move)

    def _say_chat(self, think):
        """Take everything the engine of a finished `think` state has said
        since the last frame and put it in the chat."""
        info = think.get("info")
        if info is None:
            return
        for line in info.drain():
            self._note_bot_say(think.get("name") or "Engine",
                               think.get("side") or "", line)

    @staticmethod
    def _closest(legal, move):
        """The nearest legal stand-in for a move the blurred view allowed:
        the same piece to the same square, else the same piece, else any."""
        if move is not None:
            same = [m for m in legal if m.from_sq == move.from_sq]
            if same:
                return next((m for m in same if m.to_sq == move.to_sq),
                            random.choice(same))
        return random.choice(legal)

    def caption_button_rects(self):
        """Hit areas for the drawn minimize/maximize/close caption buttons;
        mirrors the geometry xp.titlebar paints."""
        w = self.window[0]
        top = (TITLEBAR_H - 16) // 2
        close = pygame.Rect(w - 16 - 3, top, 16, 16)
        maximize = close.move(-(16 + 3), 0)
        minimize = close.move(-2 * (16 + 3), 0)
        return minimize, maximize, close

    # ------------------------------------------------------- menu bar & about

    MENU_DEFS = const.MENU_DEFS

    # View menu label -> settings key it shows or hides
    VIEW_TOGGLES = const.VIEW_TOGGLES

    def menubar_rects(self):
        """Hit areas matching the labels xp.menubar paints."""
        rects, x = [], 6
        for label, _ in self.MENU_DEFS:
            w = self.small_font.size(label)[0] + 12
            rects.append(pygame.Rect(x, TITLEBAR_H + 2, w, MENUBAR_H - 4))
            x += w + 2
        return rects

    def _menu_hidden(self, text):
        """Items that only exist in certain modes are removed from the popup
        entirely, as opposed to _menu_enabled which greys an always-shown
        item. "New Game" appears only once a game has actually ended; "Bot
        Dev" only when the developer toggle is on."""
        if text == "New Game":
            return not (self.state == GAME and self.game is not None
                        and bool(self.game.result()))
        if text == "Bot Dev":
            return not self.settings.get("show_bot_dev")
        if text == "Server stats...":
            return not self.hosting     # only a live host has a server to view
        return False

    def _visible_entries(self, idx):
        """MENU_DEFS[idx]'s labels minus the hidden ones, with any leading,
        trailing or doubled separator collapsed so a hidden item never leaves
        a dangling line."""
        _, entries = self.MENU_DEFS[idx]
        kept = [t for t in entries if t is None or not self._menu_hidden(t)]
        out = []
        for text in kept:
            if text is None and (not out or out[-1] is None):
                continue        # drop a separator at the top or back-to-back
            out.append(text)
        while out and out[-1] is None:
            out.pop()
        return out

    def menu_popup_items(self, idx):
        """(items, popup rect); items are (text or None, rect) pairs."""
        entries = self._visible_entries(idx)
        bar = self.menubar_rects()[idx]
        wide = max([self.font.size(t)[0] for t in entries if t] or
                   [self.font.size("Menu")[0]]) + 36
        x, y = bar.left - 4, bar.bottom
        out = []
        for text in entries:
            if text is None:
                out.append((None, pygame.Rect(x + 6, y + 3, wide - 14, 1)))
                y += 8
                continue
            out.append((text, pygame.Rect(x, y, wide, 20)))
            y += 22
        return out, pygame.Rect(x - 2, bar.bottom - 1, wide + 6,
                                y - bar.bottom + 3)

    def _menu_enabled(self, text):
        """Items that only make sense with a live game are greyed out."""
        if text == "Restart lobby...":
            return self.state == GAME and self.hosting
        if self.lan_client and text in ("New Game", "Undo", "Resign"):
            return False        # the host owns the board a LAN client plays on
        if text == "End Game":
            # only meaningful while two engines are playing each other, and
            # only until the duel is stopped or already has a result
            return (self.state == GAME and self.duel()
                    and self.game is not None and not self.game.result()
                    and not self.duel_ended)
        if text in ("Undo", "Resign"):
            return (self.state == GAME and self.game is not None
                    and not self.game.result() and not self.duel())
        if text == "Export game history...":
            return self.state == GAME and bool(self.history)
        if text == "Update bots...":
            return self._browser is None
        if text == "Update client...":
            return self._client_box is None      # grey while a check runs
        return True

    def run_menu_action(self, text):
        self.open_menu = None
        if not self._menu_enabled(text):
            return
        self.sfx.play("click")
        if text == "New Game":
            self.start_game()
        elif text == "Restart lobby...":
            self.restart_lobby()
        elif text == "Export game history...":
            self.export_pgn()
        elif text == "Settings...":
            self.settings_return = self.state   # Close goes back there
            self._settings_scroll = 0
            self.settings_index = min(self.settings_index,
                                      len(self.settings_rows) - 1)
            self._open_settings()
        elif text == "Exit":
            pygame.event.post(pygame.event.Event(pygame.QUIT))
        elif text == "Fullscreen":
            self.toggle_fullscreen()
        elif text == "Undo":
            if not self.anim:
                self._undo()
        elif text == "Resign":
            self._resign()
        elif text == "End Game":
            self.end_game()
        elif text in self.VIEW_TOGGLES:
            self.settings.cycle(self.VIEW_TOGGLES[text])
            self.settings.save()
            self._layout()
        elif text == "Flip board":
            self.toggle_flip()
        elif text == "About FligChess":
            self.show_about = True
        elif text == "Server stats...":
            self.open_stats()
        elif text == "Update bots...":
            self.open_bot_browser()
        elif text == "Update client...":
            self._start_client_update()
        elif text == "Bot Dev":
            self.open_bot_dev()

    # ---------------------------------------------------------- bot browser
    # "Update bots..." and the Settings page's "Manage bots..." open a modal
    # overlay instead of the old fire-and-forget worker. It lists every bot the
    # repository knows about, plus any installed locally that are not in the
    # index, each with a state badge and Download / Update / Delete buttons.
    # A bot whose minimum app version is newer than this build has its
    # Download / Update disabled and is told to update the app first. The
    # repository is read in a worker so the UI never blocks on the network.

    def open_bot_browser(self):
        """Show the bot browser overlay and start filling it in the
        background. Safe to call from the Help menu or the Settings page."""
        self.message = None
        self._browser = {"rows": [], "error": None, "note": None,
                         "loading": True, "box": None, "action": None,
                         "scroll": 0, "center": None, "hit": [],
                         "close": None, "cap": None, "close_btn": None,
                         "retry": None, "force": None}
        self._browser_fetch()
        self.sfx.play("click")

    def close_bot_browser(self):
        """Dismiss the overlay; a running worker simply finds the box gone."""
        self._browser = None
        self._reload_bots()

    def _browser_fetch(self):
        """Read the repo index and the local bots/ folder in a worker thread;
        _poll_browser merges the finished rows back into the overlay."""
        br = self._browser
        if br is None:
            return
        box = {"rows": None, "error": None, "done": False}

        def work():
            try:
                info = bot_updater.check()
                rows, seen = [], set()
                for e in info["bots"]:
                    state = ("new" if e.get("local") is None
                             else "current" if e.get("current") else "update")
                    rows.append({"name": e["name"], "file": e["file"],
                                 "local": e.get("local"),
                                 "remote": e.get("remote"),
                                 "modes": e.get("modes"),
                                 "hash": e.get("hash", ""),
                                 "app": e.get("app", ""),
                                 "state": state,
                                 "blocked": const.bot_app_blocked(e.get("app", ""))})
                    seen.add(e["file"])
                for p in sorted(bots_dir().glob("*.py")):
                    if p.name.startswith("_") or p.name in seen:
                        continue
                    version, modes, _name = bot_updater.probe(p)
                    rows.append({"name": p.stem, "file": p.name,
                                 "local": version,
                                 "remote": None, "modes": modes, "hash": "",
                                 "app": "",
                                 "state": "local", "blocked": False})
                box["rows"] = rows
            except Exception as exc:  # noqa: BLE001 - show, never crash
                box["error"] = str(exc)
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        br["box"] = box
        br["loading"] = True

    def _browser_download(self, row):
        """Fetch one bot in a worker; re-read the list once it lands so the
        row's badge and buttons refresh with the truth on disk."""
        br = self._browser
        if br is None or br.get("action") is not None:
            return
        entry = {"name": row["name"], "file": row["file"],
                 "hash": row["hash"], "remote": row["remote"]}
        box = {"line": None, "done": False}

        def work():
            try:
                box["line"] = bot_updater.download_bot(entry)
            except Exception as exc:  # noqa: BLE001 - report, keep the box alive
                box["line"] = f"{row['name']}: failed ({exc})"
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        br["action"] = box
        br["note"] = f"{row['name']}: working..."

    def _browser_delete(self, row):
        """Remove a bot file now (a local disk op) and re-read the list."""
        if bot_updater.delete_bot(row["file"]):
            self._browser["note"] = f"Deleted {row['name']}"
            self._reload_bots()
            self._browser_fetch()

    def _poll_browser(self):
        """Collect finished background work for the browser overlay."""
        br = self._browser
        if br is None:
            return
        box = br.get("box")
        if box is not None and box.get("done"):
            br["box"] = None
            br["loading"] = False
            if box.get("error"):
                br["error"] = box["error"]
            else:
                br["rows"] = box["rows"] or []
                br["error"] = None
            self._clamp_browser_scroll()
        act = br.get("action")
        if act is not None and act.get("done"):
            br["action"] = None
            br["note"] = act.get("line")
            self._reload_bots()
            self._browser_fetch()

    # geometry --------------------------------------------------------------

    def _browser_rect(self):
        w, h = self.window
        bw = min(540, max(440, w - 40))
        bh = min(380, max(260, h - 70))
        r = pygame.Rect(0, 0, bw, bh)
        r.center = (self._browser or {}).get("center") or (w // 2, h // 2)
        return r

    def _browser_rows_area(self):
        r = self._browser_rect()
        top = r.y + 24 + 20          # caption + column headings
        bottom = r.bottom - 34       # room for the Close button
        return pygame.Rect(r.x + 8, top, r.w - 16, max(0, bottom - top))

    BOT_ROW_H = 34

    def _clamp_browser_scroll(self):
        br = self._browser
        if br is None:
            return
        area = self._browser_rows_area()
        content = len(br["rows"]) * self.BOT_ROW_H
        br["scroll"] = max(0, min(br["scroll"], max(0, content - area.height)))

    def _browser_headings(self, r):
        """Column captions above the list; the button column is left blank."""
        hy = r.y + 26
        for text, frac in zip(const.BOT_HEADINGS, const.BOT_COL_FRACS):
            img = self.small_font.render(text, True, COL_MUTED)
            self.screen.blit(img, (r.x + int(r.w * frac), hy))

    def _browser_row_buttons(self, row, rr, hit, index):
        """Right-aligned action buttons for one row, appended to `hit`. A
        blocked row shows the update-the-app note instead of a live button.
        Returns (buttons, note)."""
        br = self._browser
        busy = br.get("action") is not None
        buttons, note = [], None
        x = rr.right - 6
        if row["local"] is not None:                 # Delete, rightmost
            b = pygame.Rect(x - 58, rr.centery - 11, 58, 22)
            x = b.x - 5
            buttons.append((b, const.BOT_BTN_DELETE, True))
            hit.append((b, "delete", index, True))
        if row["state"] in ("new", "update"):         # Download / Update
            label = (const.BOT_BTN_DOWNLOAD if row["state"] == "new"
                     else const.BOT_BTN_UPDATE)
            enabled = not row["blocked"] and not busy
            if row["blocked"]:
                note = const.BOT_BLOCKED_NOTE
            bw = self.small_font.size(label)[0] + 18
            b = pygame.Rect(x - bw, rr.centery - 11, bw, 22)
            x = b.x - 5
            buttons.append((b, label, enabled))
            hit.append((b, "primary", index, enabled))
        return buttons, note

    def _browser_layout(self):
        """(panel, area, visible rows) recording every button into the hit
        table so the next click can act on it. Rows fully outside are skipped."""
        br = self._browser
        r = self._browser_rect()
        area = self._browser_rows_area()
        hit = []
        visible = []
        for i, row in enumerate(br["rows"]):
            ry = area.y + i * self.BOT_ROW_H - br["scroll"]
            rr = pygame.Rect(area.x, ry, area.width, self.BOT_ROW_H - 2)
            if rr.bottom <= area.y or rr.top >= area.bottom:
                continue
            buttons, note = self._browser_row_buttons(row, rr, hit, i)
            visible.append((row, rr, buttons, note))
        br["hit"] = hit
        return r, area, visible

    def _browser_ellipsis(self, text, px):
        """Trim `text` to `px` with a trailing ellipsis, in the board font."""
        img = self.font.render(text, True, COL_TEXT)
        if img.get_width() <= px:
            return text
        while text and self.font.size(text + "\u2026")[0] > px:
            text = text[:-1]
        return text + "\u2026"

    def _browser_draw_row(self, row, rr, buttons, note, mouse):
        cols = const.BOT_COL_FRACS
        f0, f1, f2, f3, f4 = cols
        name = self.font.render(self._browser_ellipsis(
            row["name"], int(rr.width * (f1 - f0)) - 12), True, COL_TEXT)
        self.screen.blit(name, (rr.x + 6, rr.centery - name.get_height() // 2))
        local = "-" if row["local"] is None else str(row["local"])
        remote = "-" if row["remote"] is None else str(row["remote"])
        modes = row.get("modes")
        mode_text = ", ".join(modes) if modes else "-"
        for frac, text in ((f1, local), (f2, remote), (f3, mode_text)):
            img = self.small_font.render(text, True, COL_MUTED)
            self.screen.blit(img, (rr.x + int(rr.width * frac),
                                   rr.centery - img.get_height() // 2))
        stat = const.BOT_STATE_LABELS.get(row["state"], row["state"])
        st = self.small_font.render(stat, True, COL_ACCENT_LIT)
        self.screen.blit(st, (rr.x + int(rr.width * f4),
                              rr.centery - st.get_height() // 2))
        if note:                                        # blocked: explain below
            nt = self.small_font.render(note, True, xp.TEXT_MUTED)
            self.screen.blit(nt, (rr.x + int(rr.width * f4),
                                  rr.bottom - nt.get_height()))
        for b, label, enabled in buttons:
            xp.button(self.screen, b, label, self.small_font,
                      enabled=enabled, hovered=enabled and b.collidepoint(mouse))

    def _browser_center_note(self, text):
        area = self._browser_rows_area()
        lines = text.splitlines() or [""]
        lh = self.small_font.get_height()
        y = area.centery - (len(lines) * lh) // 2
        for line in lines:
            img = self.small_font.render(line, True, COL_MUTED)
            self.screen.blit(img, (area.x + 10, y))
            y += lh

    def draw_browser(self):
        br = self._browser
        r, area, visible = self._browser_layout()
        _body, close = xp.message_frame(self.screen, r,
                                        const.BOT_BROWSER_TITLE, self.font,
                                        icon=getattr(self, "app_icon", None))
        br["close"] = close
        br["cap"] = pygame.Rect(r.x + 2, r.y + 2, r.w - 4, 24)
        mouse = pygame.mouse.get_pos()
        self._browser_headings(r)
        pygame.draw.rect(self.screen, xp.FIELD, area)
        pygame.draw.rect(self.screen, xp.DK_SHADOW, area, 1)
        old_clip = self.screen.get_clip()
        self.screen.set_clip(area)
        if br.get("loading"):
            self._browser_center_note(const.BOT_LOADING)
        elif br.get("error"):
            self._browser_center_note("Could not reach the repository:\n"
                                      + str(br["error"]))
        elif not br["rows"]:
            self._browser_center_note(const.BOT_NONE_LOCAL)
        else:
            for row, rr, buttons, note in visible:
                self._browser_draw_row(row, rr, buttons, note, mouse)
        self.screen.set_clip(old_clip)
        # bottom strip, left to right: retry (only when the fetch failed),
        # the force-all-modes checkbox, then the last action's result line
        x = r.x + 8
        cy = area.bottom + 15
        br["retry"] = None
        if br.get("error"):
            rb = pygame.Rect(x, cy - 11, 90, 22)
            xp.button(self.screen, rb, const.BOT_BTN_RETRY, self.font,
                      hovered=rb.collidepoint(mouse))
            br["retry"] = rb
            x = rb.right + 10
        br["force"] = xp.checkbox(self.screen, (x, cy),
                                  bool(self.settings.get("allow_all_modes")),
                                  const.BOT_FORCE_MODES, self.small_font)
        if not br.get("error") and br.get("note"):
            img = self.small_font.render(self._browser_ellipsis(
                br["note"], max(40, r.right - 12 - br["force"].right)),
                True, COL_ACCENT_LIT)
            self.screen.blit(img, (br["force"].right + 12,
                                   cy - img.get_height() // 2))
        cb = pygame.Rect(r.right - 96, r.bottom - 30, 88, 24)
        xp.button(self.screen, cb, const.BOT_BTN_CLOSE, self.font,
                  hovered=cb.collidepoint(mouse))
        br["close_btn"] = cb

    def handle_browser_event(self, event):
        """The browser overlay owns every event while it is up."""
        br = self._browser
        if event.type == pygame.MOUSEWHEEL:
            br["scroll"] -= event.y * self.BOT_ROW_H
            self._clamp_browser_scroll()
            return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close_bot_browser()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            r = self._browser_rect()
            if br.get("close") and br["close"].collidepoint(pos):
                self.close_bot_browser()
            elif br.get("close_btn") and br["close_btn"].collidepoint(pos):
                self.close_bot_browser()
            elif br.get("retry") and br["retry"].collidepoint(pos):
                self._browser_fetch()
            elif br.get("force") and br["force"].collidepoint(pos):
                self._toggle_force_modes()
            elif br.get("cap") and br["cap"].collidepoint(pos):
                self._drag = {"kind": "browser",
                              "off": (pos[0] - r.centerx, pos[1] - r.centery)}
            else:
                matched = False
                for rect, kind, index, enabled in br["hit"]:
                    if rect.collidepoint(pos):
                        matched = True
                        if enabled:
                            row = br["rows"][index]
                            if kind == "delete":
                                self._browser_delete(row)
                            elif kind == "primary":
                                self._browser_download(row)
                        break
                if not matched and not r.collidepoint(pos):
                    self.close_bot_browser()     # clicking away dismisses it
            return True
        if event.type == pygame.MOUSEMOTION:
            self._update_drag(event)
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._drag = None
            return True
        return True

    def _toggle_force_modes(self):
        """Flip 'force allow all modes' and store it. The browser redraws
        from the setting each frame, so no row data has to change."""
        self.settings.cycle("allow_all_modes")
        self.settings.save()
        self.sfx.play("click")

    # ------------------------------------------------------ server stats
    # A host-only overlay opened by the View menu or the waiting room's Stats
    # button. It reads Host.stats_snapshot() live every frame (the numbers
    # move as clients poll) and paints a summary of the server, a scrollable
    # read-only table of every connected session, and the shared chat log
    # grouped by sender with the pawn-colour marker. Everything scrolls in one
    # list; there are no row buttons - only the caption and the Close button.

    def open_stats(self):
        """Show the server-stats overlay for the live host; a no-op when no
        server is up (the menu item is hidden then anyway)."""
        if self.host is None:
            return
        self.message = None
        self._stats = {"scroll": 0, "center": None, "hit": [], "close": None,
                       "cap": None, "close_btn": None}
        self.sfx.play("click")

    def close_stats(self):
        self._stats = None

    def _stats_rect(self):
        w, h = self.window
        bw = min(560, max(440, w - 40))
        bh = min(420, max(280, h - 70))
        r = pygame.Rect(0, 0, bw, bh)
        r.center = (self._stats or {}).get("center") or (w // 2, h // 2)
        return r

    # header (mode/port, uptime/url) sits above the column captions, which
    # sit above the scrollable rows; a Close button reserves the bottom strip
    STATS_HEAD_H = 44
    STATS_ROW_H = 40
    STATS_MSG_H = 18
    STATS_SEC_H = 22

    def _stats_rows_area(self):
        r = self._stats_rect()
        top = r.y + 24 + self.STATS_HEAD_H + 20      # caption + header + heads
        bottom = r.bottom - 34                        # the Close button strip
        return pygame.Rect(r.x + 8, top, r.w - 16, max(0, bottom - top))

    def _stats_items(self, snap):
        """Flatten the snapshot into one scrollable list of render items:
        each client becomes a two-line row, then a divider, then the chat log
        grouped by sender. Returns a list of dicts the draw pass understands."""
        items = []
        clients = snap.get("clients", [])
        if not clients:
            items.append({"kind": "empty", "text": const.STATS_NO_CLIENTS,
                          "h": self.STATS_ROW_H})
        for c in clients:
            items.append({"kind": "client", "c": c, "h": self.STATS_ROW_H})
        items.append({"kind": "section", "text": const.STATS_CHAT,
                      "h": self.STATS_SEC_H})
        for m in snap.get("messages", []):
            items.append({"kind": "msg", "m": m, "h": self.STATS_MSG_H})
        return items

    def _stats_content_h(self, items):
        return sum(it["h"] for it in items)

    def _clamp_stats_scroll(self, items):
        st = self._stats
        if st is None:
            return
        area = self._stats_rows_area()
        st["scroll"] = max(0, min(st["scroll"],
                                  max(0, self._stats_content_h(items)
                                      - area.height)))

    def _fmt_uptime(self, secs):
        secs = int(max(0, secs))
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m {s}s"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"

    def _status_badge(self, snap):
        if snap.get("game_over"):
            return const.STATS_STATUS_OVER
        if snap.get("status") == "waiting" or not snap.get("has_guest"):
            return const.STATS_STATUS_WAITING
        return const.STATS_STATUS_PLAYING

    def _stats_headings(self, r):
        cols = ((const.STATS_HEADINGS[0], 0.02),
                (const.STATS_HEADINGS[1], 0.30),
                (const.STATS_HEADINGS[2], 0.44),
                (const.STATS_HEADINGS[3], 0.62),
                (const.STATS_HEADINGS[4], 0.72),
                (const.STATS_HEADINGS[5], 0.88))
        for text, frac in cols:
            img = self.small_font.render(text, True, COL_MUTED)
            self.screen.blit(img, (r.x + int(r.w * frac), r.y + 26))

    def _draw_client_row(self, c, rr):
        """One session as two lines: the columns on top, the address details
        and the approximation note underneath."""
        role = const.STATS_ROLE_LABELS.get(c.get("role", ""),
                                           c.get("role", "") or "?")
        ip = c.get("ip", "")
        reqs = str(c.get("requests", 0))
        rtt = "%.0f" % c.get("rtt", 0.0)
        err = "%.1f" % c.get("error_rate", 0.0)
        name = self.font.render(self._browser_ellipsis(c.get("name", ""),
                                                       int(rr.width * 0.26)),
                                True, COL_TEXT)
        self.screen.blit(name, (rr.x + 6, rr.y + 2))
        for frac, text in ((0.30, role), (0.44, ip), (0.62, reqs),
                           (0.72, rtt), (0.88, err)):
            img = self.small_font.render(text, True, COL_TEXT)
            self.screen.blit(img, (rr.x + int(rr.width * frac), rr.y + 4))
        # second line: bytes moved, when it was last seen, and the agent
        seen = int(time.time() - c.get("last_seen", time.time()))
        total = c.get("bytes_in", 0) + c.get("bytes_out", 0)
        detail = const.STATS_ROW_DETAIL.format(
            agent=self._short_agent(c.get("user_agent", "")),
            bytes=total, seen=seen)
        d = self.tiny_font.render(self._browser_ellipsis(detail, rr.width - 12),
                                  True, COL_MUTED)
        self.screen.blit(d, (rr.x + 6, rr.bottom - d.get_height() - 2))

    def _short_agent(self, ua):
        ua = (ua or "").strip()
        if not ua:
            return "-"
        return ua[:48]

    def _draw_msg_row(self, m, rr):
        mark = self._chat_pawn(m.get("color"))
        x = rr.x + 6
        if mark is not None:
            self.screen.blit(mark, (x, rr.centery - mark.get_height() // 2))
            x += mark.get_width() + 3
        line = "%s: %s" % (m.get("who", "?"), m.get("text", ""))
        img = self.small_font.render(
            self._browser_ellipsis(line, rr.width - (x - rr.x) - 6),
            True, COL_TEXT)
        self.screen.blit(img, (x, rr.centery - img.get_height() // 2))

    def draw_stats(self):
        st = self._stats
        snap = self.host.stats_snapshot() if self.host is not None else {}
        r = self._stats_rect()
        _body, close = xp.message_frame(self.screen, r,
                                        const.STATS_TITLE, self.font,
                                        icon=getattr(self, "app_icon", None))
        st["close"] = close
        st["cap"] = pygame.Rect(r.x + 2, r.y + 2, r.w - 4, 24)
        area = self._stats_rows_area()
        # the server summary line above the table: mode + port, then uptime + url
        mode = (const.STATS_MODE_WEB if snap.get("web")
                else const.STATS_MODE_LAN)
        line1 = ("%s  -  %s %s  -  %s" % (mode, const.STATS_PORT,
                                          snap.get("port", ""),
                                          self._status_badge(snap)))
        line2 = ("%s %s  -  %s" % (const.STATS_UPTIME,
                                   self._fmt_uptime(snap.get("uptime", 0)),
                                   snap.get("url", "")))
        for i, text in enumerate((line1, line2)):
            img = self.small_font.render(text, True, COL_TEXT)
            self.screen.blit(img, (r.x + 10, r.y + 24 + i * 18))
        self._stats_headings(r)
        items = self._stats_items(snap)
        self._clamp_stats_scroll(items)
        pygame.draw.rect(self.screen, xp.FIELD, area)
        pygame.draw.rect(self.screen, xp.DK_SHADOW, area, 1)
        old_clip = self.screen.get_clip()
        self.screen.set_clip(area)
        y = area.y - st["scroll"]
        for it in items:
            rr = pygame.Rect(area.x, y, area.width, it["h"])
            if rr.bottom > area.y and rr.top < area.bottom:
                if it["kind"] == "client":
                    self._draw_client_row(it["c"], rr)
                elif it["kind"] == "msg":
                    self._draw_msg_row(it["m"], rr)
                elif it["kind"] == "empty":
                    img = self.small_font.render(it["text"], True, COL_MUTED)
                    self.screen.blit(img, (rr.x + 8, rr.centery
                                           - img.get_height() // 2))
                elif it["kind"] == "section":
                    head = self.small_font.render(
                        self._browser_ellipsis(it["text"], area.width),
                        True, COL_ACCENT_LIT)
                    self.screen.blit(head, (rr.x + 6,
                                            rr.centery - head.get_height() // 2))
            y += it["h"]
        # the approximation footnote lives just under the scroll area
        note = self.tiny_font.render(const.STATS_APPROX_NOTE, True,
                                      xp.TEXT_MUTED)
        self.screen.set_clip(old_clip)
        self.screen.blit(note, (r.x + 10, area.bottom + 6))
        cb = pygame.Rect(r.right - 96, r.bottom - 30, 88, 24)
        mouse = pygame.mouse.get_pos()
        xp.button(self.screen, cb, const.STATS_CLOSE, self.font,
                  hovered=cb.collidepoint(mouse))
        st["close_btn"] = cb

    def handle_stats_event(self, event):
        """The stats overlay owns every event while it is up: read-only, so
        the only actions are scroll, drag, and the caption/Close dismiss."""
        st = self._stats
        if event.type == pygame.MOUSEWHEEL:
            st["scroll"] -= event.y * self.STATS_ROW_H
            snap = self.host.stats_snapshot() if self.host is not None else {}
            self._clamp_stats_scroll(self._stats_items(snap))
            return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close_stats()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            r = self._stats_rect()
            if (st.get("close") and st["close"].collidepoint(pos)) or \
               (st.get("close_btn") and st["close_btn"].collidepoint(pos)):
                self.close_stats()
            elif st.get("cap") and st["cap"].collidepoint(pos):
                self._drag = {"kind": "stats",
                              "off": (pos[0] - r.centerx, pos[1] - r.centery)}
            elif not r.collidepoint(pos):
                self.close_stats()          # clicking away dismisses it
            return True
        if event.type == pygame.MOUSEMOTION:
            self._update_drag(event)
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._drag = None
            return True
        return True

    # ------------------------------------------------------- client updates

    def _start_client_update(self):
        """Check for a newer packaged build in a worker thread. On the way up
        it only compares versions; the swap-and-relaunch happens only once the
        main loop has collected a positive result, so the UI never blocks on
        the network (mirrors the bot browser's background fetch)."""
        box = {"note": None, "done": False, "error": None,
               "update": False}

        def work():
            try:
                info = selfupdate.check()
                if not info.get("newer"):
                    box["note"] = (f"You are already on the newest version "
                                   f"({info['current']}).")
                else:
                    box["update"] = True
                    box["note"] = selfupdate.update_and_relaunch()
            except Exception as exc:  # noqa: BLE001 - show, never crash
                box["error"] = str(exc)
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self._client_box = box

    def _poll_client_update(self):
        box = getattr(self, "_client_box", None)
        if box is None or not box.get("done"):
            return
        self._client_box = None
        if box.get("error"):
            self.message = ("Update client",
                            f"Update failed:\n{box['error']}")
        elif box.get("update"):
            # a packaged build has staged its replacement and handed off to a
            # detached helper; quit so the helper can finish the swap and
            # relaunch. The note explains what is happening as we close.
            self.message = ("Update client", box.get("note") or "")
            pygame.event.post(pygame.event.Event(pygame.QUIT))
        else:
            self.message = ("Update client", box.get("note") or "")

    # ------------------------------------------------------------- bot dev
    # A developer-only screen, reached from the File menu when the matching
    # setting is on. Two tabs: the bot API reference (read live off
    # fligchess.core.bot_api) and the read-only source of every installed
    # bot. Everything here is text meant to be looked at, never edited.

    def open_bot_dev(self):
        """Show the Bot Dev screen, resting on the docs tab. `self.bots` is
        already loaded, so the source tab starts on the first installed bot."""
        self._botdev_tab = 0
        self._botdev_scroll = 0
        self._botdev_bot = next(iter(self.bots), None)
        self.state = BOTDEV

    def _botdev_tabs(self):
        return ("Docs", "Installed bots")

    def _botdev_tab_rects(self):
        """The two tab buttons, laid out left-aligned under the menu bar."""
        cr = self.client_rect()
        rects = []
        x = cr.x + 12
        for label in self._botdev_tabs():
            w = self.font.size(label)[0] + 26
            rects.append(pygame.Rect(x, cr.top + 10, w, 24))
            x += w + 8
        return rects

    def _botdev_content(self):
        """The sunken text area both tabs draw their body inside."""
        cr = self.client_rect()
        top = cr.top + 42
        return pygame.Rect(cr.x + 12, top, cr.width - 24,
                           max(40, cr.bottom - top - 12))

    def _botdev_bot_rects(self):
        """One button per installed bot, wrapped across rows at the top of
        the source tab; empty on the docs tab or with no bots installed."""
        if self._botdev_tab != 1 or not self.bots:
            return []
        box = self._botdev_content()
        out = []
        x, y = box.x + 6, box.y + 6
        for name in self.bots:
            w = self.small_font.size(name)[0] + 16
            if x + w > box.right - 6 and x > box.x + 6:
                x, y = box.x + 6, y + 26
            out.append((name, pygame.Rect(x, y, w, 22)))
            x += w + 6
        return out

    def _botdev_text_top(self):
        """Where the scrollable lines start: just under the bot buttons on
        the source tab, near the top of the panel on the docs tab."""
        rects = self._botdev_bot_rects()
        box = self._botdev_content()
        if rects:
            return max(r.bottom for _n, r in rects) + 8
        return box.y + 6

    def _botdev_text_lines(self):
        """The lines the current tab shows, from bot_docs."""
        if self._botdev_tab == 1:
            cls = self.bots.get(self._botdev_bot)
            if cls is None:
                return ["no bots are installed"]
            return bot_docs.bot_source_lines(cls)
        return bot_docs.api_lines()

    def _botdev_max_scroll(self):
        box = self._botdev_content()
        lh = self.mono_font.get_height()
        view = max(0, box.bottom - self._botdev_text_top() - 6)
        return max(0, len(self._botdev_text_lines()) * lh - view)

    def _clamp_botdev_scroll(self):
        self._botdev_scroll = max(0, min(self._botdev_scroll,
                                         self._botdev_max_scroll()))

    def handle_botdev_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.state = self.settings_return   # back under the menu bar
                return
            if event.key == pygame.K_TAB:
                self._botdev_tab = (self._botdev_tab + 1) % len(self._botdev_tabs())
                self._botdev_scroll = 0
                return
            lh = self.mono_font.get_height()
            if event.key in (pygame.K_UP, pygame.K_w):
                self._botdev_scroll = max(0, self._botdev_scroll - lh)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self._botdev_scroll += lh
                self._clamp_botdev_scroll()
        elif event.type == pygame.MOUSEWHEEL:
            self._botdev_scroll -= event.y * self.mono_font.get_height() * 3
            self._clamp_botdev_scroll()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self._botdev_tab_rects()):
                if rect.collidepoint(event.pos):
                    if self._botdev_tab != i:
                        self._botdev_tab = i
                        self._botdev_scroll = 0
                    return
            for name, rect in self._botdev_bot_rects():
                if rect.collidepoint(event.pos):
                    self._botdev_bot = name
                    self._botdev_scroll = 0
                    return

    def draw_botdev(self):
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        mouse = pygame.mouse.get_pos()
        for i, (label, rect) in enumerate(
                zip(self._botdev_tabs(), self._botdev_tab_rects())):
            xp.button(self.screen, rect, label, self.font,
                      default=(self._botdev_tab == i),
                      hovered=(self._botdev_tab != i
                               and rect.collidepoint(mouse)))
        box = self._botdev_content()
        xp.field(self.screen, box)
        # the bot-name buttons sit over the panel's top on the source tab
        for name, rect in self._botdev_bot_rects():
            xp.button(self.screen, rect, name, self.small_font,
                      default=(name == self._botdev_bot),
                      hovered=name != self._botdev_bot
                      and rect.collidepoint(mouse))
        self._clamp_botdev_scroll()
        lh = self.mono_font.get_height()
        top = self._botdev_text_top()
        old_clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(box.x + 3, top, box.width - 6,
                                         max(0, box.bottom - 3 - top)))
        y = top - self._botdev_scroll
        for line in self._botdev_text_lines():
            if y + lh >= top:
                img = self.mono_font.render(line, True, COL_TEXT)
                self.screen.blit(img, (box.x + 6, y))
            y += lh
            if y > box.bottom:
                break
        self.screen.set_clip(old_clip)
        note = self.tiny_font.render(
            "read-only - Esc to go back, Tab to switch, wheel to scroll",
            True, COL_MUTED)
        self.screen.blit(note, (box.x + 4, cr.bottom - note.get_height() - 2))

    def _reload_bots(self):
        """Re-scan bots/ so the fresh files are importable right away, and
        settle the seats onto what the new folder actually holds."""
        self._update_avail = None
        self.bots = load_bots(bots_dir())
        self.players = self.settings["players"] = self._clean_players(
            self.players)

    def _start_update_probe(self):
        """Opt-in silent background check: config/updater.json containing
        {"auto_check": true}. Only a positive result is shown, on the menu
        screen - never a popup when up to date, offline or unconfigured."""
        if not bot_updater.load_settings().get("auto_check"):
            return
        box = {"done": False, "stale": []}

        def work():
            try:
                info = bot_updater.check()
                box["stale"] = [b["name"] for b in info["bots"]
                                if not b["current"]]
            except Exception:  # noqa: BLE001 - a failed probe stays silent
                pass
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self._probe_box = box

    def _poll_update_probe(self):
        box = self._probe_box
        if box is None or not box.get("done"):
            return
        self._probe_box = None
        if box.get("stale"):
            self._update_avail = ", ".join(box["stale"])

    def _resign(self):
        """The human side gives up: whoever is to move when they are not a
        bot (never reachable in an AI vs AI duel, the menu item is greyed)."""
        if self.state != GAME or self.game is None or self.game.result():
            return
        turn = self.game.board.turn
        loser = turn if not self._side_bot(turn) else next(
            c for c in (WHITE, BLACK) if not self._side_bot(c))
        self.game.resigned = loser
        self._think = None
        self.over_dismissed = False
        if not self.anim:
            self.show_over = True
            self.sfx.play("end")

    def end_game(self):
        """Stop an AI-vs-AI duel where it stands and show the result overlay.
        The board stays visible; the engines do not think again until a new
        game starts, which is the point of the menu item when there is no
        Resign or Undo to reach during a self-play."""
        if not (self.state == GAME and self.duel() and self.game is not None):
            return
        self.duel_ended = True
        self._think = None
        self.over_dismissed = False
        if not self.anim:
            self.show_over = True
            self.sfx.play("end")

    def _guest_resign(self):
        """The browser guest gave up from their Resign button: their colour
        loses, whoever is to move. Only ever reached while hosting."""
        self.game.resigned = self.guest_color
        self._think = None
        self.over_dismissed = False
        if not self.anim:
            self.show_over = True
            self.sfx.play("end")

    # -------------------------------------------------------- pgn export

    def pgn_text(self):
        """Portable Game Notation of the open game: the seven standard
        header tags, the wrapped SAN move list and the result token. A
        four-army cross game uses its own compact form (see `_pgn_text4`)."""
        if self.four:
            return self._pgn_text4()
        result = {WHITE_WINS: "1-0", BLACK_WINS: "0-1",
                  DRAW: "1/2-1/2"}.get(self.game.result(), "*")
        white = self.player_name(WHITE)
        black = self.player_name(BLACK)
        date = time.strftime("%Y.%m.%d")
        head = ['[Event "FligChess game"]',
                '[Site "FligChess"]',
                f'[Date "{date}"]',
                '[Round "1"]',
                f'[White "{white}"]',
                f'[Black "{black}"]',
                f'[Result "{result}"]',
                f'[Variant "{getattr(self.variant, "key", "classic")}"]',
                '[Players "2"]']
        plies = [h["san"] for h in self.history]
        moves = []
        for i in range(0, len(plies), 2):
            pair = f"{i // 2 + 1}. {plies[i]}"
            if i + 1 < len(plies):
                pair += f" {plies[i + 1]}"
            moves.append(pair)
        body = textwrap.fill(" ".join(moves), 75)
        return "\n".join(head) + "\n\n" + \
            (body + "\n" if body else "") + result + "\n"

    def _seat4_name(self, army):
        """Who plays a cross army, for a PGN name tag: its seat's own name,
        or the engine seated on it, or the army's colour word."""
        seat = (self.seats4 or {}).get(army, {})
        return (seat.get("name") or seat.get("bot")
                or const.ARMY_LABELS.get(army, army))

    def _pgn_text4(self):
        """Four-player PGN. The tags name all four armies and say it is a
        cross game; the move list reads as compact coordinate tokens,
        `"<army> <from>-<to>[=P]"`, chosen over SAN so the spectator can
        replay them cheaply and without lag. The result token is the winning
        army's name, a draw when the no-progress cap ended it, or '*' still
        running."""
        res = self.game.result()
        if res == DRAW4:
            result = "1/2-1/2"
        elif res:
            result = "%s wins" % ARMY_NAMES[res]
        else:
            result = "*"
        head = ['[Event "FligChess game"]',
                '[Site "FligChess"]',
                '[Date "%s"]' % time.strftime("%Y.%m.%d"),
                '[Round "1"]',
                '[White "%s"]' % self._seat4_name("w"),
                '[Black "%s"]' % self._seat4_name("b"),
                '[Red "%s"]' % self._seat4_name("r"),
                '[Blue "%s"]' % self._seat4_name("bl"),
                '[Result "%s"]' % result,
                '[Variant "%s"]' % getattr(self.variant, "key", "classic"),
                '[Players "4"]']
        tokens = []
        for mv, h in zip(self.game.moves, self.history):
            token = "%s %s-%s" % (h.get("mover", ""),
                                  _cross_sq_name(mv.from_sq),
                                  _cross_sq_name(mv.to_sq))
            if mv.promotion:
                token += "=" + mv.promotion.upper()
            tokens.append(token)
        body = textwrap.fill(" ".join(tokens), 75)
        return "\n".join(head) + "\n\n" + \
            (body + "\n" if body else "") + result + "\n"

    def _pgn_save_path(self):
        """Ask for a file name through a small Tk save dialog; where there
        is no display or no tkinter, drop the file in the home folder.
        Returns None when the user cancels."""
        name = f"FligChess_{time.strftime('%Y%m%d_%H%M%S')}.pgn"
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            try:
                root.withdraw()
                root.attributes("-topmost", True)
            except Exception:
                pass
            path = filedialog.asksaveasfilename(
                title="Export game history", initialfile=name,
                defaultextension=".pgn",
                filetypes=[("PGN chess file", "*.pgn")])
            root.destroy()
            return path or None
        except Exception:
            return str(pathlib.Path.home() / name)

    def export_pgn(self):
        if self.state != GAME or self.game is None:
            return
        path = self._pgn_save_path()
        if not path:
            return
        try:
            pathlib.Path(path).write_text(self.pgn_text(), encoding="utf-8")
        except OSError as exc:
            self.message = ("Export game history",
                            f"Could not save the file:\n{exc}")
        else:
            self.message = ("Export game history",
                            f"Game history saved to\n"
                            f"{pathlib.Path(path).name}")

    def draw_menu_popup(self):
        items, popup = self.menu_popup_items(self.open_menu)
        self.screen.fill(xp.FIELD, popup)
        pygame.draw.rect(self.screen, xp.DK_SHADOW, popup, 1)
        mouse = pygame.mouse.get_pos()
        for text, r in items:
            if text is None:
                pygame.draw.line(self.screen, xp.SHADOW, (r.x, r.y),
                                 (r.right, r.y))
                pygame.draw.line(self.screen, xp.HILITE, (r.x, r.y + 1),
                                 (r.right, r.y + 1))
                continue
            enabled = self._menu_enabled(text)
            cell = r.inflate(-4, 0).move(2, 0)
            hover = enabled and cell.collidepoint(mouse)
            if hover:
                self.screen.fill(xp.SELECTION, cell)
            color = ((255, 255, 255) if hover
                     else xp.TEXT_MUTED if not enabled else xp.TEXT)
            img = self.font.render(text, True, color)
            self.screen.blit(img, (r.x + 24, r.centery - img.get_height() // 2))
            checked = ((text == "Fullscreen" and self.fullscreen)
                       or (text == "Flip board" and self.flipped)
                       or (text in self.VIEW_TOGGLES
                           and self.settings[self.VIEW_TOGGLES[text]]))
            if checked:
                xp.checkbox(self.screen, (r.x + 6, r.centery), True, "",
                            self.font)

    def message_rect(self):
        """Small XP info box used by the export confirmation and errors."""
        w, h = self.window
        _title, text = self.message
        lines = text.splitlines() or [""]
        lh = self.font.get_height()
        r = pygame.Rect(0, 0, min(360, max(240, w - 40)),
                        24 + 16 + len(lines) * (lh + 2) + 38)
        r.center = self.message_pos or (w // 2, h // 2)
        return r

    def message_ok_rect(self):
        r = self.message_rect()
        return pygame.Rect(r.centerx - 44, r.bottom - 34, 88, 24)

    def draw_message(self):
        r = self.message_rect()
        title, text = self.message
        body, close = xp.message_frame(self.screen, r, title, self.font,
                                       icon=getattr(self, "app_icon", None))
        self._msg_close = close
        self._msg_cap = pygame.Rect(r.x + 2, r.y + 2, r.w - 4, 24)
        y = body.y + 12
        for line in text.splitlines():
            img = self.font.render(line, True, COL_TEXT)
            self.screen.blit(img, (max(body.x + 12,
                                       r.centerx - img.get_width() // 2), y))
            y += img.get_height() + 2
        xp.button(self.screen, self.message_ok_rect(), const.BTN_OK,
                  self.font,
                  default=True)

    def about_rect(self):
        w, h = self.window
        r = pygame.Rect(0, 0, min(350, max(220, w - 20)), 158)
        r.center = self.about_pos or (w // 2, h // 2)
        return r

    def about_ok_rect(self):
        r = self.about_rect()
        return pygame.Rect(r.centerx - 44, r.bottom - 40, 88, 24)

    def draw_about(self):
        r = self.about_rect()
        body, close = xp.message_frame(self.screen, r, "About FligChess",
                                       self.font,
                                       icon=getattr(self, "app_icon", None))
        self._about_close = close
        self._about_cap = pygame.Rect(r.x + 2, r.y + 2, r.w - 4, 24)
        lines = (f"FligChess {APP_VERSION}", "a chess app & embeddable bot",
                 "made with pygame, styled like Windows XP")
        # render the text block first, then set the logo beside it clear of
        # every line - the old fixed text offset let the wide sprite overlap
        # the words, which is what made them glitch into the image
        imgs = [(self.big_font if i == 0 else self.small_font).render(
            line, True, COL_TEXT if i == 0 else COL_MUTED)
            for i, line in enumerate(lines)]
        gap = 3
        block_h = sum(im.get_height() for im in imgs) + gap * (len(imgs) - 1)
        lx = body.x + 16
        text_x = lx + self.logo.get_width() + 14
        top = body.y + 16
        self.screen.blit(self.logo,
                         (lx, top + (block_h - self.logo.get_height()) // 2))
        y = top
        for im in imgs:
            self.screen.blit(im, (text_x, y))
            y += im.get_height() + gap
        xp.button(self.screen, self.about_ok_rect(), const.BTN_OK, self.font,
                  default=True)

    def handle_event(self, event):
        if self._browser is not None:
            # the bot browser is modal: it owns every event while it is up
            self.handle_browser_event(event)
            return
        if self._stats is not None:
            # the server-stats overlay is modal too
            self.handle_stats_event(event)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.qr_open:
                self.qr_open = False
                return
            if self.message:
                self.message = None
                return
            if self.show_about:
                self.show_about = False
                return
            if self.open_menu is not None:
                self.open_menu = None
                return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            # keep _win_pending: the last target still lands via run()
            self._drag = None
            return
        if event.type == pygame.MOUSEMOTION:
            self._update_drag(event)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._chrome_press(event.pos):
                return            # caption buttons / move / resize consumed it
            if self._dialog_press(event.pos):
                return            # about / game-over caption + close
            if self.open_menu is not None:
                items, popup = self.menu_popup_items(self.open_menu)
                if popup.collidepoint(event.pos):
                    for text, r in items:
                        if text and r.collidepoint(event.pos):
                            self.run_menu_action(text)
                            return
                    return            # padding click keeps the menu open
                self.open_menu = None
                for i, bar in enumerate(self.menubar_rects()):
                    if bar.collidepoint(event.pos):
                        self.open_menu = i
                        return
                return                      # outside click just closes it
            for i, bar in enumerate(self.menubar_rects()):
                if bar.collidepoint(event.pos):
                    self.open_menu = i
                    return
        if self.state == MENU:
            self.handle_menu_event(event)
        elif self.state == VARIANT:
            self.handle_choice_event(event)
        elif self.state == LANMODE:
            self.handle_lan_mode_event(event)
        elif self.state == HOST:
            self.handle_host_event(event)
        elif self.state == LANWAIT:
            self.handle_lan_wait_event(event)
        elif self.state == JOIN:
            self.handle_join_event(event)
        elif self.state == PLAYERS:
            self.handle_player_event(event)
        elif self.state == SETTINGS:
            self.handle_settings_event(event)
        elif self.state == BOTDEV:
            self.handle_botdev_event(event)
        elif self.state == GAME:
            self.handle_game_event(event)

    # ---------------------------------------------------- chrome interactions

    def press_maximize(self):
        """Caption maximize: real XP maximize when we own the window;
        falls back to fullscreen where SDL window tricks are unavailable."""
        if self._sdlwin() or self._maximized:
            self.toggle_maximize()
        else:
            self.toggle_fullscreen()

    def resize_edges(self, pos):
        """Which of the four thin window edges the cursor grabs, if any."""
        if not self.windowed() or self._maximized:
            return None
        w, h = self.window
        g = 5
        edges = "".join(n for n, on in (("l", pos[0] < g),
                                        ("r", pos[0] > w - g),
                                        ("t", pos[1] < g),
                                        ("b", pos[1] > h - g)) if on)
        return edges or None

    def _begin_window_drag(self, kind, pos, edges=None):
        """Anchor a move/resize drag on window geometry. When SDL can read
        the desktop cursor the drag anchors on it - stable even when the
        cursor leaves the window. When it cannot (Wayland, dummy driver) the
        drag falls back to in-window mouse motion, re-anchored in
        `_apply_window_drag` after every applied step so the window's own
        movement never feeds back into the cursor reading."""
        win = self._sdlwin()
        if win is None:
            return False
        try:
            p0 = win.position
        except Exception:
            return False
        g0 = self.global_mouse()
        d = {"kind": kind, "g0": g0, "p0": p0, "s0": self.window,
             "edges": edges or ""}
        if g0 is None:
            d["base"] = tuple(p0)
            d["bsize"] = tuple(self.window)
            d["m_anchor"] = pygame.mouse.get_pos()
        self._drag = d
        return True

    def _chrome_press(self, pos):
        """Handle presses on the drawn caption / window edges."""
        minimize, maximize, close = self.caption_button_rects()
        if close.collidepoint(pos):
            pygame.event.post(pygame.event.Event(pygame.QUIT))
            return True
        if maximize.collidepoint(pos):
            self.press_maximize()
            return True
        if minimize.collidepoint(pos):
            pygame.display.iconify()
            return True
        edges = self.resize_edges(pos)
        if edges:
            return self._begin_window_drag("resize", pos, edges)
        if pygame.Rect(0, 0, self.window[0], TITLEBAR_H).collidepoint(pos):
            self.open_menu = None
            now = pygame.time.get_ticks() / 1000.0
            if (self._last_cap and now - self._last_cap[0] < 0.45
                    and abs(pos[0] - self._last_cap[1][0]) < 8):
                self._last_cap = None
                self._drag = None
                self._win_pending = None
                self.press_maximize()
                return True
            self._last_cap = (now, pos)
            if not self._maximized:
                self._begin_window_drag("move", pos)
            return True
        return False

    def _dialog_press(self, pos):
        """Close buttons and caption drags on the in-app dialogs."""
        if self.qr_open and self.state in (GAME, LANWAIT):
            box, _img = self.qr_rect()
            if ((self._qr_close and self._qr_close.collidepoint(pos))
                    or self.qr_ok_rect().collidepoint(pos)):
                self.qr_open = False
            elif self.qr_restart_rect().collidepoint(pos):
                self.restart_lobby()
            elif self.qr_copylink_rect().collidepoint(pos):
                if self.host:
                    self._copy_to_clipboard(self.host.share_url)
            elif self.qr_copyaddr_rect().collidepoint(pos):
                if self.host:
                    self._copy_to_clipboard(self.host.url)
            elif self._qr_cap and self._qr_cap.collidepoint(pos):
                pass                     # the share box is fixed, undraggable
            elif not box.collidepoint(pos):
                self.qr_open = False       # clicking away dismisses it
            return True                    # modal: nothing else gets the click
        if self.message:
            r = self.message_rect()
            if ((self._msg_close and self._msg_close.collidepoint(pos))
                    or self.message_ok_rect().collidepoint(pos)):
                self.message = None
            elif self._msg_cap and self._msg_cap.collidepoint(pos):
                self._drag = {"kind": "message",
                              "off": (pos[0] - r.centerx,
                                      pos[1] - r.centery)}
            elif not r.collidepoint(pos):
                self.message = None       # clicking away dismisses it
            return True                   # the info box is modal
        if self.show_about:
            r = self.about_rect()
            if ((self._about_close and self._about_close.collidepoint(pos))
                    or self.about_ok_rect().collidepoint(pos)):
                self.show_about = False
            elif self._about_cap and self._about_cap.collidepoint(pos):
                self._drag = {"kind": "about",
                              "off": (pos[0] - r.centerx,
                                      pos[1] - r.centery)}
            elif not r.collidepoint(pos):
                self.show_about = False   # clicking away dismisses the box
            return True                   # the message box is modal
        if (self.state == GAME and self.game and self.show_over
                and not self.anim):
            panel = self.over_panel_rect()
            if self._over_close and self._over_close.collidepoint(pos):
                self.show_over = False
                self.over_dismissed = True   # board stays visible behind it
                return True
            if self._over_cap and self._over_cap.collidepoint(pos):
                self._drag = {"kind": "over",
                              "off": (pos[0] - panel.centerx,
                                      pos[1] - panel.centery)}
                return True
        return False

    def _panel_size(self, kind):
        """On-screen size of an in-window dialog (message / about / over /
        browser / stats). Each rect is built around its stored centre, but
        width and height never depend on the centre, so this reads the true
        half-extents we need to keep a dragged popup inside the window."""
        rect = {"message": self.message_rect, "about": self.about_rect,
                "over": self.over_panel_rect, "browser": self._browser_rect,
                "stats": self._stats_rect}[kind]()
        return rect.size

    def _clamp_center(self, center, size):
        """Clamp a dialog centre so the whole panel stays within the window
        rectangle. A panel wider or taller than the window simply centres on
        that axis rather than getting pinned off-screen."""
        w, h = self.window
        pw, ph = size

        def one(c, span, full):
            # pygame puts a rect's top at centre - span//2, so its far edge
            # lands at centre + (span - span//2): bound the centre by that
            # true half so an odd-sized panel never spills one pixel over
            lo, hi = span // 2, full - (span - span // 2)
            if hi < lo:
                return full // 2
            return max(lo, min(int(c), hi))

        return (one(center[0], pw, w), one(center[1], ph, h))

    def _update_drag(self, event):
        d = self._drag
        if not d:
            return
        # a mouse-up delivered *outside* this window never arrives, so also
        # end the drag when motion comes in with the button no longer held
        # (synthetic test events carry no buttons attribute: assume held)
        buttons = getattr(event, "buttons", (1, 0, 0))
        if not buttons[0]:
            self._drag = None
            self._win_pending = None
            return
        if d["kind"] in ("over", "about", "message"):
            center = (event.pos[0] - d["off"][0], event.pos[1] - d["off"][1])
            setattr(self, f"{d['kind']}_pos",
                    self._clamp_center(center, self._panel_size(d["kind"])))
            return
        if d["kind"] == "browser" and self._browser is not None:
            center = (event.pos[0] - d["off"][0], event.pos[1] - d["off"][1])
            self._browser["center"] = self._clamp_center(
                center, self._panel_size("browser"))
            return
        if d["kind"] == "stats" and self._stats is not None:
            center = (event.pos[0] - d["off"][0], event.pos[1] - d["off"][1])
            self._stats["center"] = self._clamp_center(
                center, self._panel_size("stats"))
            return
        # window move/resize: only *record* the new geometry; the SDL calls
        # happen in _apply_window_drag, outside the event pump (see run())
        win = self._sdlwin()
        if win is None:
            return
        if d["g0"] is not None:
            g = self.global_mouse()
            if g is None:
                return
            dx, dy = g[0] - d["g0"][0], g[1] - d["g0"][1]
            x0, y0, x1, y1 = self._virtual_desktop()
            if abs(dx) > x1 - x0 or abs(dy) > y1 - y0:
                return            # implausible jump from a bad reading
            base, bsize = d["p0"], d["s0"]
        else:
            # Wayland / dummy driver: no desktop cursor, so drive the drag off
            # in-window motion against anchors re-set after every applied step
            m = pygame.mouse.get_pos()
            dx, dy = m[0] - d["m_anchor"][0], m[1] - d["m_anchor"][1]
            base, bsize = d["base"], d["bsize"]
        if d["kind"] == "move":
            x, y = self._clamp_pos(base[0] + dx, base[1] + dy, bsize[0])
            self._win_pending = {"pos": (x, y)}
            return
        e = d["edges"]
        x, y = base
        w, h = bsize
        if "r" in e:
            w = max(MIN_WINDOW[0], w + dx)
        if "b" in e:
            h = max(MIN_WINDOW[1], h + dy)
        if "l" in e:
            right = x + w          # the far edge stays pinned
            w = max(MIN_WINDOW[0], w - dx)
            x = right - w
        if "t" in e:
            bottom = y + h
            h = max(MIN_WINDOW[1], h - dy)
            y = bottom - h
        self._win_pending = {"size": (int(w), int(h)),
                             "pos": (int(x), int(y))}

    def _clamp_pos(self, x, y, w):
        """Keep at least part of the caption on *some* monitor: the window
        may live on any screen of a multi-monitor desktop, but never
        vanishes completely. Virtual-desktop coords, so x may be negative."""
        x0, y0, x1, y1 = self._virtual_desktop()
        x = max(x0 + 60 - w, min(int(x), x1 - 60))
        y = max(y0, min(int(y), y1 - TITLEBAR_H))
        return (int(x), int(y))

    def draw(self):
        self.draw_chrome()
        if self.state == MENU:
            self.draw_menu()
        elif self.state == SETTINGS:
            self.draw_settings()
        elif self.state == VARIANT:
            self.draw_choice()
        elif self.state == LANMODE:
            self.draw_lan_mode()
        elif self.state == HOST:
            self.draw_host()
        elif self.state == LANWAIT:
            self.draw_lan_wait()
        elif self.state == JOIN:
            self.draw_join()
        elif self.state == PLAYERS:
            self.draw_players()
        elif self.state == BOTDEV:
            self.draw_botdev()
        else:
            self.draw_game4() if self.four else self.draw_game()
        if self.open_menu is not None:
            self.draw_menu_popup()
        if self.show_about:
            self.draw_about()
        if self.qr_open and self.state in (GAME, LANWAIT):
            self.draw_qr()
        if self.message:
            self.draw_message()
        if self._browser is not None:
            self.draw_browser()
        if self._stats is not None:
            self.draw_stats()

    # -------------------------------------------------------- XP window chrome

    def client_rect(self):
        """Usable area between the menu bar and the status bar."""
        w, h = self.window
        return pygame.Rect(0, self.chrome_top, w,
                           max(40, h - self.chrome_top - self.bottom_h))

    def window_title(self):
        """Caption text: the app, plus the role this window holds in the game.
        A host always says so; a joined app is a Player or a Spectator; only a
        plain offline game names the arrangement instead."""
        if self.state not in (GAME, LANWAIT) or self.game is None:
            return "FligChess"
        if self.hosting:
            return f"FligChess - {self.variant.label} - {const.ROLE_HOST}"
        if self.lan_client:
            role = (const.ROLE_SPECTATOR if self.client_is_viewer
                    else const.ROLE_PLAYER)
            return f"FligChess - {self.variant.label} - {role}"
        mode = (const.FOUR_MODE_LABEL if self.four
                else MODE_LABELS.get(self._mode_key(), "FligChess"))
        return f"FligChess - {self.variant.label} - {mode}"

    def draw_chrome(self):
        """Paint the app as one big XP window: caption, menu bar, grip bar."""
        w, h = self.window
        self.screen.fill(COL_BG)
        cap = pygame.Rect(0, 0, w, TITLEBAR_H)
        xp.titlebar(self.screen, cap, self.window_title(), self.font,
                    getattr(self, "app_icon", None))
        bar = pygame.Rect(0, cap.bottom, w, MENUBAR_H)
        xp.menubar(self.screen, bar, active=self.open_menu,
                   font=self.small_font,
                   labels=tuple(label for label, _ in self.MENU_DEFS))
        xp.statusbar(self.screen, pygame.Rect(0, h - self.bottom_h, w,
                                              self.bottom_h), [],
                     self.small_font)

    # ------------------------------------------------------------ start menu

    def menu_rects(self):
        cr = self.client_rect()
        rects = []
        bw = max(112, min(160, cr.width // 3))
        bh, gap = 26, 8
        x = cr.right - bw - 14
        y = cr.top + max(14, cr.height // 9)
        for _ in self.menu_items:
            rects.append(pygame.Rect(x, y, bw, bh))
            y += bh + gap
        return rects

    def handle_menu_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_w):
                self.menu_index = (self.menu_index - 1) % len(self.menu_items)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.menu_index = (self.menu_index + 1) % len(self.menu_items)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.pick_menu_item(self.menu_index)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self.menu_rects()):
                if rect.collidepoint(event.pos):
                    self.pick_menu_item(i)

    def pick_menu_item(self, index):
        self.sfx.play("click")
        label = self.menu_items[index]
        if label == "Play Offline":
            self.open_setup()
        elif label == "LAN":
            # one hosting flow: pick how many play, then the settings, then
            # wait; whether a browser is offered is a setting, not a menu item
            self.open_lan_mode()
        elif label == "Join LAN":
            self.open_join()
        elif label == "Settings":
            self.settings_return = MENU         # reached from the menu screen
            self._settings_scroll = 0
            self._open_settings()
        else:
            pygame.event.post(pygame.event.Event(pygame.QUIT))

    # --------------------------------------------------------- game setup
    # Play walks two setup screens before a game is reachable at all: which
    # rules the board uses, then who plays under them.

    def open_setup(self):
        """Play opens on the rules page, resting on the rules it remembers."""
        self.state = VARIANT
        self.variant_index = variants.VARIANTS.index(self.settings["variant"])

    def choice_keys(self):
        """The rules on offer, Back excluded."""
        return variants.VARIANTS

    def choice_labels(self):
        """Row labels of the rules page; the last row is Back."""
        return [variants.info(key)[0] for key in self.choice_keys()] + [BACK]

    def _choice_count(self):
        """Rows the page shows, Back included."""
        return len(self.choice_labels())

    def _back_index(self):
        """The row index of the Back button."""
        return self._choice_count() - 1

    def choice_index(self):
        """The highlighted row."""
        return self.variant_index

    def set_choice(self, index):
        """Highlight row `index`, Back included - the line below the buttons
        always explains whichever row the cursor is actually on."""
        self.variant_index = max(0, min(index, self._back_index()))

    def choice_note(self):
        """One line explaining the highlighted row, or the Back button."""
        if self.choice_index() == self._back_index():
            return BACK_NOTE
        return variants.info(self.choice_keys()[self.choice_index()])[1]

    def choice_layout(self):
        """(heading y, note y, button rects), the whole block centred in the
        client area. The line explaining the highlighted row sits between the
        heading and the buttons: below them it would end up glued to Back,
        which is no place for a line about whatever the cursor is on."""
        cr = self.client_rect()
        labels = self.choice_labels()
        bw = max(180, min(300, cr.width - 40))
        bh, gap = 28, 10
        head_gap, note_gap = 8, 16      # heading to line, line to the buttons
        head_h = self.title_font.get_height()
        note_h = self.small_font.get_height()
        block = head_h + head_gap + note_h + note_gap + len(labels) * bh
        block += (len(labels) - 1) * gap
        top = cr.top + max(12, (cr.height - block) // 2)
        x = cr.x + (cr.width - bw) // 2
        first = top + head_h + head_gap + note_h + note_gap
        rects = [pygame.Rect(x, first + i * (bh + gap), bw, bh)
                 for i in range(len(labels))]
        return top, top + head_h + head_gap, rects

    def choice_rects(self):
        return self.choice_layout()[2]

    def handle_choice_event(self, event):
        count = self._choice_count()
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_w):
                self.set_choice((self.choice_index() - 1) % count)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.set_choice((self.choice_index() + 1) % count)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                               pygame.K_SPACE):
                self.pick_choice(self.choice_index())
            elif event.key == pygame.K_ESCAPE:
                self.pick_choice(self._back_index())      # Back
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self.choice_rects()):
                if rect.collidepoint(event.pos):
                    self.pick_choice(i)

    def pick_choice(self, index):
        """A row of the rules page was chosen."""
        self.sfx.play("click")
        self.set_choice(index)
        if index == self._back_index():         # Back
            self.state = MENU
            return
        self.settings["variant"] = self.choice_keys()[index]
        self.settings.save()
        # the rules say nothing about who plays, so the seats keep whatever
        # they were set to - which engine, its options and the names are all
        # settled on the page this one leads to
        self.open_players()

    def draw_choice(self):
        """The rules page: a heading, a line that explains the highlighted
        rule, a button per variant, and Back - XP setup style."""
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        head_y, note_y, rects = self.choice_layout()
        head = self.title_font.render("Which rules?", True, xp.CAP_MID)
        self.screen.blit(head, (cr.centerx - head.get_width() // 2, head_y))
        note = self.small_font.render(self.choice_note(), True, COL_ACCENT_LIT)
        self.screen.blit(note, (cr.centerx - note.get_width() // 2, note_y))
        mouse = pygame.mouse.get_pos()
        for i, (label, rect) in enumerate(zip(self.choice_labels(), rects)):
            if rect.collidepoint(mouse):
                self.set_choice(i)
            xp.button(self.screen, rect, label, self.font,
                      default=i == self.choice_index(),
                      hovered=rect.collidepoint(mouse))
        hint = self.small_font.render(
            "arrows + enter, or click   esc goes back", True, COL_MUTED)
        self.screen.blit(hint, self.bottom_text_rect(hint))

    # =========================================================== web hosting
    # "Web LAN" opens a single setup page - the rules and everything the
    # little http server needs - and starts a game whose far seat belongs to
    # whoever scans the QR code. The app stays the only authority: it
    # publishes the position to the server each tick and plays back whatever
    # the guest asks for, so the rules never leave the game thread.

    HOST_ROWS = (("variant", const.LAN_ROW_RULES),
                 ("color", const.LAN_ROW_YOU),
                 ("host", const.LAN_ROW_YOURNAME),
                 ("hostai", const.LAN_ROW_HOSTPLAYS),
                 ("web", const.LAN_ROW_BROWSER),
                 ("password", const.LAN_ROW_PASSWORD),
                 ("spectate", const.LAN_ROW_SPECTATE),
                 ("lock", const.LAN_ROW_LOCK),
                 ("port", const.LAN_ROW_PORT))
    HOST_ROW_H = 24
    HOST_GAP = 8
    HOST_LABEL_W = 96
    HOST_BUTTONS_H = 46
    HOST_TEXT = ("host", "password", "port")
    # a cross game's per-army seat row: key "seat_<army>", value one of
    # const.LAN_SEAT_VALUES (engine, web seat, app seat)
    SEAT_PREFIX = "seat_"
    # the engine row that follows an army set to Computer: key
    # "seatbot_<army>", value a bot name; the built-in mover is gone, so a
    # computer army always names a real, gate-allowed engine
    SEAT_BOT_PREFIX = "seatbot_"

    def host_rows(self):
        """The settings page's rows for the game being set up. A two-player
        game uses the plain set; a cross game adds one row per army the host
        does not hold, each choosing whether that side is an engine, a seat
        for browsers or a seat for other FligChess apps - and, for an engine,
        which engine."""
        rows = list(self.HOST_ROWS)
        form = self.host_form or {}
        if form.get("players") == 4:
            host_army = form.get("color", "w")
            seat_rows = []
            for army in _ARMY_IDS:
                if army == host_army:
                    continue
                seat_rows.append((self.SEAT_PREFIX + army,
                                  const.LAN_ROW_SEAT % const.ARMY_LABELS[army]))
                if form.get(self.SEAT_PREFIX + army) == "ai":
                    seat_rows.append(
                        (self.SEAT_BOT_PREFIX + army,
                         const.LAN_ROW_SEAT_BOT % const.ARMY_LABELS[army]))
            at = next((i for i, (key, _) in enumerate(rows)
                       if key == "hostai"), len(rows) - 1) + 1
            rows[at:at] = seat_rows
        return tuple(rows)

    def seat_kind(self, army):
        """Who the setup page says plays a cross army's side."""
        return (self.host_form or {}).get(self.SEAT_PREFIX + army, "ai")

    def _host_bots(self, players=None):
        """Engines the host page offers for the side the host itself plays:
        a human (None) first, then every installed bot that will play the shape
        of game being hosted. `players` falls back to the wizard's own choice."""
        if players is None:
            players = 4 if (self.host_form or {}).get("players") == 4 else 2
        return [None] + self._bots_for_mode(self._mode_token(players, "lan"))

    def _host_seat_bots(self):
        """Engines the host page offers for a cross army's computer seat: only
        those allowed to play a four-player LAN game. The built-in mover is
        gone, so a computer army must be backed by a real engine."""
        variant = (self.host_form or {}).get("variant", self.variant.key)
        return self._bots_for_mode("4p/lan/%s" % variant)

    def _new_host_form(self, prev=None):
        """The settings page's values. Re-opening keeps what was typed but
        always offers a fresh free port, since the last one may be taken. The
        player is never named here - a guest names themselves on joining - and
        'web' decides whether a browser is offered (the old Web/Host split)."""
        prev = prev or {}
        web = prev.get("web", True)
        form = {"variant": prev.get("variant", self.settings.get("variant", "classic")),
                "color": prev.get("color", "w"),
                "host": prev.get("host", self.settings.get("host_name", "")),
                "hostai": prev.get("hostai"),
                "web": web,
                "password": prev.get("password", ""),
                "spectate": prev.get("spectate", True),
                "lock": prev.get("lock", self.settings.get("password_after_join", True)),
                "port": str(free_port())}
        # a cross game remembers who plays each side, defaulting the opposite
        # corner to the kind of client actually offered and the flank armies
        # to engines - the shape the two-player flow has always produced
        for army in _ARMY_IDS:
            form[self.SEAT_PREFIX + army] = prev.get(
                self.SEAT_PREFIX + army,
                "web" if army == "b" and web else "ai")
            # an engine army keeps the engine it was named with, so walking
            # away from the page and back does not quietly drop it; a fresh
            # army defaults to the engine that plays this cross game
            form[self.SEAT_BOT_PREFIX + army] = prev.get(
                self.SEAT_BOT_PREFIX + army) or self._default_engine(
                    "4p/lan/%s" % form["variant"])
        return form

    def _server_tls(self):
        """A server TLS context from the stored certificate, or None for a
        plain http server. https is on by default; the certificate is made and
        renewed automatically, so hosting never asks the user to fetch one."""
        if not self.settings.get("https", True):
            return None
        try:
            info = cert.ensure(bits=self.settings.get("cert_bits", 2048),
                               days=self.settings.get("cert_days", 825))
            return cert.server_context(info)
        except Exception as exc:  # noqa: BLE001 - fall back to http, never fail
            self.message = ("HTTPS", "Could not secure the server, so it will "
                            "run without encryption:\n%s" % exc)
            return None

    def open_lan_mode(self):
        """Start the LAN wizard on the mode page: how many armies play. Two is
        wired end to end now; four is remembered and its board arrives in the
        four-player engine milestone, so the choice carries through the flow."""
        self.state = LANMODE
        self.menu_index = 1 if self.lan_players == 4 else 0

    def lan_mode_rects(self):
        """The two mode buttons plus a Back row, stacked and centred."""
        cr = self.client_rect()
        labels = [const.LAN_MODE_LABELS[2], const.LAN_MODE_LABELS[4],
                  const.LAN_MODE_BACK]
        bw = max(180, min(300, cr.width - 40))
        bh, gap = 28, 10
        block = len(labels) * bh + (len(labels) - 1) * gap
        top = cr.top + max(60, (cr.height - block) // 2)
        x = cr.x + (cr.width - bw) // 2
        return [pygame.Rect(x, top + i * (bh + gap), bw, bh)
                for i in range(len(labels))]

    def handle_lan_mode_event(self, event):
        rects = self.lan_mode_rects()
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_w):
                self.menu_index = (self.menu_index - 1) % len(rects)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.menu_index = (self.menu_index + 1) % len(rects)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                               pygame.K_SPACE):
                self._pick_lan_mode(self.menu_index)
            elif event.key == pygame.K_ESCAPE:
                self.state = MENU
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(rects):
                if rect.collidepoint(event.pos):
                    self._pick_lan_mode(i)
                    return

    def _pick_lan_mode(self, index):
        if index == 0:
            self._enter_lan_players(2)
        elif index == 1:
            self._enter_lan_players(4)
        else:
            self.sfx.play("click")
            self.state = MENU

    def _enter_lan_players(self, count):
        """Record how many armies play and move on to the settings page."""
        self.sfx.play("select")
        self.lan_players = count
        self.open_host_setup()

    def draw_lan_mode(self):
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        head = self.title_font.render(const.LAN_MODE_TITLE, True, xp.CAP_MID)
        self.screen.blit(head, (cr.centerx - head.get_width() // 2, cr.y + 8))
        rects = self.lan_mode_rects()
        labels = [const.LAN_MODE_LABELS[2], const.LAN_MODE_LABELS[4],
                  const.LAN_MODE_BACK]
        mouse = pygame.mouse.get_pos()
        for i, (rect, label) in enumerate(zip(rects, labels)):
            xp.button(self.screen, rect, label, self.font, default=i == 0,
                      hovered=rect.collidepoint(mouse) or i == self.menu_index)
        if self.menu_index == 1:
            note = const.LAN_MODE_NOTE[4]
        elif self.menu_index == 2:
            note = BACK_NOTE
        else:
            note = const.LAN_MODE_NOTE[2]
        img = self.small_font.render(note, True, COL_MUTED)
        self.screen.blit(img, self.bottom_text_rect(img))

    def open_host_setup(self):
        """Enter the settings page from the mode page; nothing is typed yet."""
        self.host_form = self._new_host_form(self.host_form)
        self.host_form["players"] = self.lan_players
        self.state = HOST
        self.host_index = 0
        self._host_text = None

    def host_is_text(self, key):
        return key in self.HOST_TEXT

    def host_value_label(self, key):
        value = self.host_form[key]
        if key == "variant":
            return variants.info(value)[0]
        if key == "color":
            if (self.host_form or {}).get("players") == 4:
                return const.ARMY_LABELS.get(value, const.ARMY_LABELS["w"])
            return const.VALUE_WHITE if value == "w" else const.VALUE_BLACK
        if key.startswith(self.SEAT_BOT_PREFIX):
            return value or "none installed"
        if key.startswith(self.SEAT_PREFIX):
            return const.LAN_SEAT_LABELS.get(value, value)
        if key in ("spectate", "lock", "web"):
            return const.VALUE_ON if value else const.VALUE_OFF
        if key == "hostai":
            return value or const.TYPE_LABELS[HUMAN]
        return value

    def host_step(self, key, step):
        """Left/right arrows walk a choose-one-of-several row."""
        if key == "variant":
            opts = list(variants.VARIANTS)
            i = opts.index(self.host_form["variant"])
            self.host_form["variant"] = opts[(i + step) % len(opts)]
        elif key == "color":
            if self.host_form.get("players") == 4:
                # a cross game has four corners, not two colours: any army
                # may be the host's, the rest fall to the seat rows below
                opts = list(_ARMY_IDS)
                cur = self.host_form["color"]
                i = opts.index(cur) if cur in opts else 0
                self.host_form["color"] = opts[(i + step) % len(opts)]
            else:
                self.host_form["color"] = ("b" if self.host_form["color"] == "w"
                                           else "w")
        elif key.startswith(self.SEAT_PREFIX):
            opts = list(const.LAN_SEAT_VALUES)
            cur = self.seat_kind(key[len(self.SEAT_PREFIX):])
            i = opts.index(cur) if cur in opts else 0
            self.host_form[key] = opts[(i + step) % len(opts)]
        elif key.startswith(self.SEAT_BOT_PREFIX):
            # only the engines that play this cross game: there is no built-in
            # mover to hand an army back to
            opts = self._host_seat_bots()
            cur = self.host_form.get(key)
            i = opts.index(cur) if cur in opts else 0
            self.host_form[key] = opts[(i + step) % len(opts)]
        elif key == "spectate":
            self.host_form["spectate"] = not self.host_form["spectate"]
        elif key == "lock":
            self.host_form["lock"] = not self.host_form["lock"]
        elif key == "web":
            self.host_form["web"] = not self.host_form["web"]
        elif key == "hostai":
            # the host playing its own side (None) first, then every engine
            # that will play this shape of game; with no bots there is nothing
            # to seat, so the row simply stays on the host's own hand
            opts = self._host_bots()
            cur = self.host_form["hostai"]
            i = opts.index(cur) if cur in opts else 0
            self.host_form["hostai"] = opts[(i + step) % len(opts)]
        self.sfx.play("select")

    def host_type(self, text):
        key = self._host_text
        field = self.host_form
        if key == "host":
            field[key] = (field[key] + text)[:NAME_LIMIT]
        elif key == "password":
            field[key] = (field[key] + text)[:24]
        elif key == "port":
            digits = "".join(c for c in text if c.isdigit())
            field[key] = (field[key] + digits)[:5]

    def host_erase(self):
        key = self._host_text
        self.host_form[key] = self.host_form[key][:-1]

    # ----------------------------------------------------------- its layout

    def host_group_rect(self):
        cr = self.client_rect()
        bw = min(340, cr.width - 40)
        x = cr.centerx - bw // 2
        top = cr.top + 44
        room = cr.bottom - self.HOST_BUTTONS_H - top - 8
        need = 20 + len(self.host_rows()) * (self.HOST_ROW_H + self.HOST_GAP) + 4
        return pygame.Rect(x, top, bw, min(room, need))

    def host_row_rect(self, index):
        col = self.host_group_rect()
        pitch = self.HOST_ROW_H + self.HOST_GAP
        return pygame.Rect(col.x + 10, col.y + 20 + index * pitch,
                           col.w - 20, self.HOST_ROW_H)

    def host_box_rect(self, index):
        r = self.host_row_rect(index)
        return pygame.Rect(r.x + self.HOST_LABEL_W, r.y,
                           max(40, r.w - self.HOST_LABEL_W), r.h)

    def host_buttons(self):
        cr = self.client_rect()
        y = cr.bottom - self.HOST_BUTTONS_H
        start = pygame.Rect(cr.centerx - 100, y, 96, 26)
        return start, start.move(104, 0)

    # -------------------------------------------------------------- events

    def handle_host_event(self, event):
        if event.type == pygame.TEXTINPUT and self._host_text:
            if event.text in ("\b", "\x7f"):   # some platforms carry it here
                self.host_erase()
            elif event.text.isprintable():
                self.host_type(event.text)
            return
        if event.type == pygame.KEYDOWN:
            self.handle_host_key(event.key, getattr(event, "scancode", 0))
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.handle_host_click(event.pos)

    def handle_host_key(self, key, scancode=0):
        rows = self.host_rows()
        if self._host_text:                       # a field owns the keyboard
            if self._erase_key(key, scancode):
                self.host_erase()
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB,
                         pygame.K_ESCAPE):
                self._host_text = None
            return
        focus_key = rows[self.host_index % len(rows)][0]
        if key in (pygame.K_UP, pygame.K_w):
            self.host_index = (self.host_index - 1) % len(rows)
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.host_index = (self.host_index + 1) % len(rows)
        elif key in (pygame.K_LEFT, pygame.K_a):
            if not self.host_is_text(focus_key):
                self.host_step(focus_key, -1)
        elif key in (pygame.K_RIGHT, pygame.K_d):
            if not self.host_is_text(focus_key):
                self.host_step(focus_key, 1)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            # enter only commits or leaves a text field; starting is the
            # button's job, so a combo row does nothing here
            if self.host_is_text(focus_key):
                self._host_text = focus_key
        elif key == pygame.K_SPACE:
            if self.host_is_text(focus_key):
                self._host_text = focus_key
            else:
                self.host_step(focus_key, 1)
        elif self._erase_key(key, scancode):
            # backspacing the highlighted field edits it even before it was
            # clicked into: the first press focuses it, then eats a character
            if self.host_is_text(focus_key):
                self._host_text = focus_key
                self.host_erase()
        elif key == pygame.K_ESCAPE:
            self.open_lan_mode()

    def handle_host_click(self, pos):
        next_btn, back = self.host_buttons()
        if next_btn.collidepoint(pos):
            self.host_new_game()
            return
        if back.collidepoint(pos):
            self.open_lan_mode()
            return
        for i, (key, _label) in enumerate(self.host_rows()):
            if not self.host_box_rect(i).collidepoint(pos):
                continue
            self.host_index = i
            if self.host_is_text(key):
                self._host_text = key
            else:
                self._host_text = None
                self.host_step(key, 1)
            return
        self._host_text = None

    # ----------------------------------------------------------------- draw

    def draw_host(self):
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        head = self.title_font.render(const.LAN_SETUP_TITLE, True, xp.CAP_MID)
        self.screen.blit(head, (cr.centerx - head.get_width() // 2, cr.y + 8))
        group = self.host_group_rect()
        xp.groupbox(self.screen, group, const.LAN_SETUP_GROUP,
                    self.small_font)
        mouse = pygame.mouse.get_pos()
        rows = self.host_rows()
        for i, (key, label) in enumerate(rows):
            row = self.host_row_rect(i)
            box = self.host_box_rect(i)
            # hover deliberately does NOT move the keyboard focus: a resting
            # cursor would keep stealing it from the arrow keys and Enter
            caption = self._fit_text(self.small_font, label,
                                     self.HOST_LABEL_W - 6)
            self.screen.blit(caption, (row.x + 2,
                                       row.centery - caption.get_height() // 2))
            self.draw_host_control(key, box, mouse)
            if rows[self.host_index % len(rows)][0] == key:
                pygame.draw.rect(self.screen, (60, 60, 60), row.inflate(4, 2), 1)
        start, back = self.host_buttons()
        xp.button(self.screen, start, const.LAN_NEXT, self.font, default=True,
                  hovered=start.collidepoint(mouse))
        xp.button(self.screen, back, const.LAN_BACK, self.font,
                  hovered=back.collidepoint(mouse))
        note = self.small_font.render(
            const.LAN_SETUP_NOTE, True, COL_MUTED)
        self.screen.blit(note, self.bottom_text_rect(note))

    def draw_host_control(self, key, box, mouse):
        if self.host_is_text(key):
            xp.field(self.screen, box)
            text = self.host_form[key]
            shown = self.host_value_label(key)
            if not shown and key == "password":
                shown = "(none)"
            elif not shown:
                shown = ""
            color = COL_TEXT if text else COL_MUTED
            img = self._fit_text(self.font, shown, box.w - 10, color)
            self.screen.blit(img, (box.x + 4,
                                   box.centery - img.get_height() // 2))
            if self._host_text == key:
                x = box.x + 6 + img.get_width()
                pygame.draw.line(self.screen, COL_TEXT, (x, box.y + 3),
                                 (x, box.bottom - 4))
        else:
            xp.combo(self.screen, box, self.host_value_label(key), self.font,
                     hovered=box.collidepoint(mouse))

    # ------------------------------------------------------------- lifecycle

    def host_new_game(self):
        """Bring the server up and step into the waiting room. The seats are
        rebuilt for this: the host side is a local human - or the engine
        chosen on the settings page - and a guest side is a remote seat no
        click or bot ever drives. In a two-player game the two colours are
        shared by one seat, whichever client takes it. In a four-player game
        the host may hold any of the four armies and every other army is
        decided on the setup page: an engine, or a seat opened for browsers,
        or one opened for other FligChess apps - so several people can be
        seated at once, each on their own side. The game does not begin here:
        it waits for the players to take their seats and for the host to
        press Play."""
        form = self.host_form
        try:
            port = int(form["port"])
        except ValueError:
            port = free_port()
        self.settings["variant"] = form["variant"]
        self.settings.save()
        self.lan_mode = not form.get("web", True)   # browser offered or not
        four = form.get("players", self.lan_players) == 4
        host_name = (form["host"] or "You")[:NAME_LIMIT]
        guest_name = "Guest"                 # a player names themselves
        mode = "%dp/lan/%s" % (4 if four else 2, form["variant"])
        host_ai = form.get("hostai")
        if host_ai not in self.bots or not self._bot_allows(host_ai, mode):
            host_ai = None               # the host plays its own side
        host_seat = {"type": AI if host_ai else HUMAN, "bot": host_ai,
                     "config": (dict(self.settings.bot_options(
                         host_ai, self.bots[host_ai].CONFIG)) if host_ai
                         else {}),
                     "name": host_name}
        remote_seat = {"type": REMOTE, "bot": None, "config": {},
                       "name": guest_name}
        seats4 = None
        if four:
            # any army may be the host's; the local window watches through
            # the host's eyes, so fog follows whichever army it holds
            host_army = form.get("color", "w")
            if host_army not in _ARMY_IDS:
                host_army = _A_WHITE
            self.host_army = host_army
            self.host_color = WHITE if host_army == _A_WHITE else BLACK
            self.guest_color = BLACK if host_army == _A_WHITE else WHITE
            self.four = True
            self._viewer = host_army
            seats4 = {host_army: dict(host_seat)}
            open_seats = []
            for army in _ARMY_IDS:
                if army == host_army:
                    continue
                kind = self.seat_kind(army)
                if kind == "ai":
                    # the setup page may name an engine for this army; with no
                    # engine (or one that will not play this game) the seat
                    # falls to the default engine that will - there is no
                    # built-in mover now, so a computer army needs a real one
                    bot = form.get(self.SEAT_BOT_PREFIX + army)
                    if bot not in self.bots or not self._bot_allows(bot, mode):
                        bot = self._default_engine(mode)
                    seats4[army] = {"type": AI, "name": bot or "Computer",
                                    "bot": bot}
                    continue
                seats4[army] = dict(remote_seat)
                open_seats.append({"color": army, "kind": kind})
            # the seat the page shows as "the guest's": the first opened one,
            # so a game with no human seats at all keeps a sane fallback view
            self.guest_army = next((s["color"] for s in open_seats),
                                   _A_BLACK if host_army == _A_WHITE
                                   else _A_WHITE)
            self.host_seats = open_seats
            form["color"] = host_army
        else:
            self.four = False
            self.host_color = WHITE if form["color"] == "w" else BLACK
            self.guest_color = BLACK if self.host_color == WHITE else WHITE
            form["color"] = "w" if self.host_color == WHITE else "b"
            self.host_seats = None            # the server's lone open seat
            if self.host_color == WHITE:
                self.players = {"w": host_seat, "b": remote_seat}
            else:
                self.players = {"w": remote_seat, "b": host_seat}
        # every computer army in a cross game needs an allowed engine; with
        # none for this mode the game refuses to come up rather than seat a
        # mover that would see straight through the fog
        if four and any(s.get("type") == AI and not s.get("bot")
                        for s in seats4.values()):
            self.message = (const.NO_ENGINE_TITLE, const.NO_ENGINE_MSG)
            self.four = False
            return
        self.host = Host(port=port, password=form["password"],
                         spectate=form["spectate"], guest_name=guest_name,
                         host_name=host_name, tls=self._server_tls(),
                         web=form.get("web", True),
                         lock_after_join=form.get("lock", True),
                         four=four,
                         host_army=host_army if four else (
                             "w" if self.host_color == WHITE else "b"),
                         seats=self.host_seats if four else None)
        # remember the choice as the default for the next game too
        self.settings["password_after_join"] = form.get("lock", True)
        self.settings.save()
        try:
            self.host.start()
        except OSError as exc:
            self.host = None
            self.four = False
            self.players = self.settings["players"]
            self.message = (const.LAN_SETUP_TITLE,
                            "Could not open that port:\n%s" % exc)
            return
        self.chat_messages = []        # a fresh lobby starts a fresh chat
        self.chat_open = False
        self._chat_input, self._chat_focus = "", False
        if self.four:
            self.start_game4(seats4, self._viewer)
        else:
            self.start_game()
        self.hosting = True
        self.awaiting_start = True     # the clock is held until Play
        self.state = LANWAIT
        # a web host offers the share link at once, so a guest can scan and sit
        self.qr_open = bool(form.get("web", True))
        self._host_pub_t = 0.0
        self.host.publish(self._host_snapshot())

    def launch_play(self):
        """The host pressed Play and every opened seat is filled: start the
        clock. Any move asked for while waiting is dropped, the journal is
        reset to the fresh board, and the running position goes out to the
        server. Enter never reaches this - only the Play button, and only
        when the players are in."""
        if not self.lan_can_play():
            return
        self.awaiting_start = False
        while self.host.take_move() is not None:
            pass                       # nothing counts before Play
        self._host_events = []         # a clean journal for the started game
        self._host_ev_seq = 0
        self.state = GAME
        self.qr_open = False           # the party is over; get on with play
        self._host_pub_t = 0.0
        self.host.publish(self._host_snapshot())

    # ---------------------------------------------------------- waiting room

    def lan_can_play(self):
        """Play lights up only once the players are actually seated. A game
        with opened seats waits for every one of them to be held (so a cross
        lobby with two human seats needs two people); a seatless game - all
        engines - starts as soon as the host wants it."""
        if not self.host:
            return False
        if not self.host_seats:
            # the plain two-player flow: the lone shared seat must be held,
            # and a game hosted against engines alone needs nothing either
            return not self.host.seat_free() or self.host.has_guest()
        return not self.host.seat_free()

    def _seat_lines(self):
        """The waiting room's per-army roster: (army, text, is_waiting) for
        every army in turn order, so the host sees who holds which side and
        what the still-empty seats are waiting for. A cross seat reads its
        name off the server rather than off the game's own seating, so a
        player who left shows as waiting again at once."""
        held = {}
        if self.host is not None:
            for seat in self.host.seats():
                if seat["taken"]:
                    held[seat["color"]] = seat
        lines = []
        for army in _ARMY_IDS:
            if self.four:
                seat = self.seats4.get(army, {})
            else:
                seat = (self.players or {}).get(army, {})
            taken = army in held
            name = (held.get(army, {}).get("name")
                    or seat.get("name") or "")
            if army == self.host_army and self.four:
                lines.append((army, const.LAN_SEAT_HOST, False))
            elif seat.get("type") == AI and not taken:
                lines.append((army, const.LAN_SEAT_COMPUTER, False))
            elif self.four and not taken:
                kind = self.seat_kind(army)
                lines.append((army, const.LAN_SEAT_WAITING %
                              const.LAN_SEAT_LABELS.get(kind, kind), True))
            elif name:
                lines.append((army, name, False))
            else:
                lines.append((army, const.LAN_SEAT_WAITING % "player", True))
        return lines

    def lan_wait_rects(self):
        """(Play, Share, Stats, Back) along the bottom of the waiting room."""
        cr = self.client_rect()
        bw, bh, gap = 118, 30, 10
        x = cr.centerx - (bw * 4 + gap * 3) // 2
        y = cr.bottom - 58
        play = pygame.Rect(x, y, bw, bh)
        return (play, play.move(bw + gap, 0), play.move(2 * (bw + gap), 0),
                play.move(3 * (bw + gap), 0))

    def handle_lan_wait_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.stop_host()
                self.open_host_setup()
            # enter never starts: only pressing Play does
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            play, share, stats, back = self.lan_wait_rects()
            if play.collidepoint(event.pos):
                self.launch_play()
            elif share.collidepoint(event.pos):
                if self.host:
                    self.qr_open = True       # re-open the share link/address
            elif stats.collidepoint(event.pos):
                if self.host:
                    self.open_stats()         # who is connected, and how
            elif back.collidepoint(event.pos):
                self.stop_host()
                self.open_host_setup()

    def draw_lan_wait(self):
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        head = self.title_font.render(const.LAN_HOST_TITLE, True, xp.CAP_MID)
        self.screen.blit(head, (cr.centerx - head.get_width() // 2, cr.y + 8))
        ready = self.lan_can_play()
        if self.four:
            lines = self._seat_lines()
            waiting = sum(1 for _army, _text, blank in lines if blank)
            if ready:
                msg = const.LAN_HOST_READY
            else:
                msg = const.LAN_HOST_WAITING4 % (waiting,
                                                 len(self.host_seats or ()))
        else:
            msg = const.LAN_HOST_READY if ready else const.LAN_HOST_WAITING
        body = self.font.render(msg, True,
                                COL_TEXT if ready else COL_MUTED)
        self.screen.blit(body, (cr.centerx - body.get_width() // 2,
                                cr.y + 70))
        if self.host is not None:
            line = self.small_font.render(self.host.share_url, True,
                                          COL_ACCENT_LIT)
            self.screen.blit(line, (cr.centerx - line.get_width() // 2,
                                    cr.y + 100))
            watching = self.host.viewer_count()
            if watching:
                cap = self.small_font.render(const.LAN_WATCHING % watching,
                                             True, COL_MUTED)
                self.screen.blit(cap, (cr.centerx - cap.get_width() // 2,
                                        cr.y + 124))
        if self.four:
            self.draw_seat_lines(cr)
        mouse = pygame.mouse.get_pos()
        play, share, stats, back = self.lan_wait_rects()
        xp.button(self.screen, play, const.LAN_PLAY, self.font,
                  enabled=ready, default=ready,
                  hovered=ready and play.collidepoint(mouse))
        xp.button(self.screen, share, const.LAN_SHARE, self.font,
                  enabled=self.host is not None,
                  hovered=self.host is not None and share.collidepoint(mouse))
        xp.button(self.screen, stats, const.STATS_BUTTON, self.font,
                  enabled=self.host is not None,
                  hovered=self.host is not None and stats.collidepoint(mouse))
        xp.button(self.screen, back, const.LAN_BACK, self.font,
                  hovered=back.collidepoint(mouse))

    def draw_seat_lines(self, cr):
        """The cross waiting room's roster: one line per army under the
        status, coloured like a turn chip so a free seat stands out."""
        y = cr.y + 150
        for army, text, blank in self._seat_lines():
            label = self.small_font.render(
                const.LAN_SEAT_LINE % (const.ARMY_LABELS[army], text), True,
                COL_MUTED if blank else COL_TEXT)
            self.screen.blit(label, (cr.centerx - label.get_width() // 2, y))
            y += label.get_height() + 4

    def _lan_wait_tick(self, dt):
        """The waiting room publishes the starting position and relays chat,
        but plays nothing: the game has not begun until the host presses Play.
        Any move a curious guest sends is discarded, and a resignation is
        ignored, so the board is exactly as it started when Play is pressed."""
        self._host_pub_t += dt
        if self._host_pub_t >= 0.25:
            self._host_pub_t = 0.0
            self.host.publish(self._host_snapshot())
        for message in self.host.take_chat():
            self.note_chat(message)
        pick = self.host.take_pick()
        if pick:
            self._apply_pick(pick)
        self._sync_seats4()
        while self.host.take_move() is not None:
            pass
        self.host.take_resign()

    def _sync_seats4(self):
        """Mirror the server's seats into the cross game's own seating: each
        held seat names its player on that army, and a seat that came open
        again (its holder left or went quiet) drops back to waiting, so the
        host's roster and the page always agree on who holds what."""
        if not (self.four and self.hosting and self.host is not None):
            return
        held = {}
        for seat in self.host.seats():
            if seat["taken"] and seat["color"]:
                held[seat["color"]] = seat.get("name") or "Guest"
        changed = False
        for army, seat in self.seats4.items():
            if seat.get("type") != REMOTE:
                continue
            # a held seat carries its player's own name; a seat that came open
            # again goes back to the placeholder it was hosted with
            name = held.get(army) or "Guest"
            if seat.get("name") != name:
                seat["name"] = name
                changed = True
        if changed:
            self.host.publish(self._host_snapshot())

    def _remote_held(self, army):
        """Whether some client is seated on this cross army right now. False
        means the seat was never claimed or its player left (or is unreachable
        past the seat timeout), so the engine has to cover the side."""
        if self.host is None:
            return False
        for seat in self.host.seats():
            if seat["color"] == army:
                return bool(seat["taken"])
        return False

    def _apply_pick(self, pick):
        """Honour a guest's side/name ask while the lobby still waits. In a
        two-player game the player holds the server's one shared seat: a name
        renames that seat, and a colour moves them onto the side they picked -
        the host simply takes the other (engines and the board view follow).
        In a cross game each seat already holds one army and the server has
        put the player on the seat they asked for, so only the name needs
        copying across here; the roster sync picks the seat up either way. Once
        Play has been pressed an ask is dropped: the game is already seated."""
        if not (self.hosting and self.awaiting_start and self.host and pick):
            return
        name = (pick.get("name") or "").strip()[:NAME_LIMIT]
        color = pick.get("color")
        changed = False
        if name and not self.four:
            seat = next((s for s in self.players.values()
                         if s.get("type") == REMOTE), None)
            if seat is not None and seat.get("name") != name:
                seat["name"] = name
                changed = True
        if self.four:
            # a cross seat already carries its player's name and army from the
            # server, which _sync_seats4 mirrors; nothing to move here
            pass
        elif color in ("w", "b"):
            want_white = color == "w"
            if want_white == (self.host_color == WHITE):
                # the guest wants the side the host holds: swap the seats
                # whole, so the host's engine and name come along to the
                # other colour, and sit the guest on the one they picked
                self.host_color = BLACK if want_white else WHITE
                self.guest_color = WHITE if want_white else BLACK
                self.players["w"], self.players["b"] = (
                    self.players["b"], self.players["w"])
                self.bot = self._make_bot("b")
                self.white_bot = self._make_bot("w")
                self._refresh_fog()
                self.flipped = not self.flipped
                changed = True
        if changed:
            # tell the server which corner the host now holds, so the lobby
            # keeps offering the truthful set of open sides, and republish
            self.host.set_shape(
                self.four, self.host_army if self.four else
                ("w" if self.host_color == WHITE else "b"))
            self.host.publish(self._host_snapshot())

    def stop_host(self):
        """Shut the server down and put the offline seats back."""
        if self.host is not None:
            try:
                self.host.stop()
            except Exception:  # noqa: BLE001 - leaving the game must not crash
                pass
            self.host = None
        self.hosting = False
        self.awaiting_start = False
        self.qr_open = False
        self._stats = None             # the stats overlay has nothing to show
        self.four = False              # back to the two-player driver
        self.host_seats = None
        self.seats4 = {}
        self.players = self.settings["players"]

    def _host_tick(self, dt):
        """Republish the position a few times a second and play any move the
        guest has asked for, once the animation of the last one has landed."""
        self._host_pub_t += dt
        if self._host_pub_t >= 0.25:
            self._host_pub_t = 0.0
            self.host.publish(self._host_snapshot())
        for message in self.host.take_chat():      # guest chat, in arrival order
            self.note_chat(message)
        if self.host.take_resign() is not None and not self.game.result():
            self._guest_resign()          # the browser guest gave up
        if (self.game.board.turn == self.guest_color and not self.anim
                and not self.pending_promotion and not self.show_over
                and not self.game.result()):
            asked = self.host.take_move()
            if asked:
                self._play_guest_move(asked)

    def _play_guest_move(self, asked):
        frm, to = _parse_square(asked.get("from")), _parse_square(asked.get("to"))
        if frm is None or to is None:
            return
        legal = [m for m in self.game.board.legal_moves()
                 if m.from_sq == frm and m.to_sq == to]
        if not legal:
            return
        want = asked.get("promotion") or "q"
        move = next((m for m in legal if m.promotion == want), legal[0])
        self._play(move)

    def _spectator_view(self, board):
        """The union view a spectator or engine duel is shown: the names of
        the squares no standing army can see (`spec_fog`), and, for every
        square that is shown, which armies can see it (`spec_tint`) so the
        page tints each tile by team. A non-fog variant hides nothing, so both
        come back empty and the whole board shows as it always did."""
        hidden, owners = self.variant.visibility(board)
        name = _cross_sq_name if self.four else sq_name
        return {"spec_fog": [name(sq) for sq in hidden],
                "spec_tint": {name(sq): sorted(owners[sq])
                              for sq in owners}}

    def _host_snapshot(self):
        """The position as JSON, seen through the guest's eyes: fog hides
        what the variant keeps from them, and `dests` only lights up when the
        guest is genuinely due to move. A cross game answers from its own
        14x14 shape instead."""
        if self.four:
            return self._host_snapshot4()
        board = self.game.board
        guest = self.guest_color
        hidden = self.variant.hidden(board, guest)
        spec = self._spectator_view(board)
        rows = ["".join(board.get((f, rank)) or "." for f in range(8))
                for rank in range(7, -1, -1)]
        over = bool(self.game.result())
        # nothing may be played until the host has pressed Play, so while the
        # game waits the guest is offered no legal moves and no move right
        started = not self.awaiting_start
        dests = {}
        if board.turn == guest and not over and started:
            for m in board.legal_moves():
                dests.setdefault(sq_name(m.from_sq), []).append(sq_name(m.to_sq))
        last = []
        if self.last_move:
            veiled = {sq_name(s) for s in hidden}
            # a move lost in the guest's fog stays lost: highlighting either
            # of its squares would hand the guest exactly what fog hides
            last = [n for n in (sq_name(self.last_move.from_sq),
                                sq_name(self.last_move.to_sq))
                    if n not in veiled]
        return {
            "rows": rows,
            "turn": "w" if board.turn == WHITE else "b",
            "guest_color": "w" if guest == WHITE else "b",
            "you_can_move": (board.turn == guest and not over
                             and started and not self.anim
                             and not self.pending_promotion),
            "dests": dests,
            "last": last,
            "check": board.in_check(),
            "result": self.game.status_text() if over else "",
            "moves": [h["san"] for h in self.history],
            "fog": [sq_name(sq) for sq in hidden],
            "variant": self.variant.label,
            "host_name": self.player_name(self.host_color),
            "guest_name": self.player_name(guest),
            "status": ("over" if over
                       else "waiting" if not started or not self.host.has_guest()
                       else "playing"),
            "events": list(self._host_events),
            # the whole move history, so a spectator can scrub back through it;
            # the handler strips it from the seated player's own payload
            "plies": self._review,
            "can_review": True,
            # the union view for a spectator: fog over what no army sees, and
            # the teams that can see each shown tile (the handler masks the
            # grids with these)
            "spec_fog": spec["spec_fog"],
            "spec_tint": spec["spec_tint"],
        }

    def _host_snapshot4(self):
        """The cross position as JSON, seen through the guest's army: `cells`
        is a 14x14 grid (each piece a full board4 code, empty squares '.', the
        cut-out corners CROSS_OFF), `four` flags the shape and `size`/`armies`
        tell the page how to draw it. `dests` lights up only for the army the
        remote guest actually holds, once the host has pressed Play.
        Multi-seat games add `seat_views`: that same private view - legal
        squares, fog and move right - worked out for every seated army, so the
        server can hand each of its players their own board."""
        board = self.game.board
        guest = self.guest_army
        over = bool(self.game.result())
        started = not self.awaiting_start
        last = []
        if self.last_move:
            last = [_cross_sq_name(self.last_move.from_sq),
                    _cross_sq_name(self.last_move.to_sq)]
        view = self._guest_view4(board, guest, over, started, last)
        views = {army: self._guest_view4(board, army, over, started, last)
                 for army, seat in self.seats4.items()
                 if seat.get("type") == REMOTE and army != guest}
        hidden = view["hidden"]
        spec = self._spectator_view(board)
        all_views = dict(views)
        all_views[guest] = view
        names = {a: const.ARMY_LABELS[a] for a in _ARMY_IDS}
        return {
            "four": True,
            "size": _CROSS_SIZE,
            "cross_off": const.CROSS_OFF,
            "armies": list(_ARMY_IDS),
            "army_names": names,
            "cells": self._cross_snapshot(board),
            "turn": board.turn,
            "guest_color": guest,
            "you_can_move": view["you_can_move"],
            "dests": view["dests"],
            "last": view["last"],
            "check": board.in_check(board.turn) if board.turn else False,
            "result": self.game.status_text() if over else "",
            "moves": [h["san"] for h in self.history],
            "fog": [_cross_sq_name(sq) for sq in hidden],
            "variant": self.variant.label,
            "host_name": self.seats4.get(self.host_army, {}).get("name", "Host"),
            "guest_name": self.seats4.get(guest, {}).get("name", "Guest"),
            "status": ("over" if over
                       else "waiting" if not started
                       or (self.host is not None and self.host.seat_free())
                       else "playing"),
            "events": list(self._host_events),
            "plies": self._review,
            "can_review": True,
            # what each seated army may see and do, keyed by army id
            "seat_views": {army: {k: v[k] for k in
                                  ("guest_color", "dests", "fog", "last",
                                   "you_can_move", "guest_name")}
                           for army, v in all_views.items()},
            # the union view for a spectator: fog over what no army sees and
            # the teams that see each shown tile (the handler masks with these)
            "spec_fog": spec["spec_fog"],
            "spec_tint": spec["spec_tint"],
        }

    def _guest_view4(self, board, army, over, started, last):
        """One army's private view of the cross: the squares its pieces may
        play to, the cells its variant hides from it, and whether it may move
        at all. `last` is the raw pair of the last move's squares, stripped
        down to the ones this army is allowed to see."""
        hidden = self.variant.hidden(board, army)
        your_turn = (board.turn == army and not over and started)
        dests = {}
        if your_turn:
            for m in board.legal_moves():
                dests.setdefault(_cross_sq_name(m.from_sq), []).append(
                    _cross_sq_name(m.to_sq))
        veiled = {_cross_sq_name(sq) for sq in hidden}
        # a move lost in this army's fog stays lost: highlighting either of
        # its squares would hand the player exactly what the fog hides
        seen = [n for n in last if n not in veiled]
        return {"guest_color": army, "dests": dests, "hidden": hidden,
                "fog": [_cross_sq_name(sq) for sq in hidden], "last": seen,
                "you_can_move": your_turn and not self.anim,
                "guest_name": self.seats4.get(army, {}).get("name", "Guest")}

    # ------------------------------------------------------ share (QR) dialog

    def qr_rect(self):
        """The share dialog. A web host gets a scannable QR of the link; a LAN
        host (whose peer is another FligChess, not a phone browser) instead gets
        the address and password as text with copy buttons. The box is sized
        around whatever lines it has to show."""
        w, h = self.window
        show_img = self.host is not None and not self.lan_mode
        img = (self._qr_image(self.host.url, _qr_scale(w, h))
               if show_img else None)
        fh = self.font.get_height()
        pw = self.host.password if self.host else ""
        addr = self.host.share_url if self.host else ""
        text_w = self.font.size(addr)[0] + 24
        pw_w = self.font.size("Password: %s" % pw)[0] + 24 if pw else 0
        img_w = img.get_width() if img else 0
        img_h = img.get_height() if img else 0
        box_w = max(img_w, text_w, pw_w, 260) + 32
        # rows: the picture (web only), the address line, an optional password
        # line, a copy-buttons row, and the OK / Restart pair along the bottom
        box_h = 30 + img_h + fh + (fh if pw else 0) + 8 + 24 + 8 + 30
        box = pygame.Rect(0, 0, box_w, box_h)
        box.center = (w // 2, h // 2)
        return box, img

    def qr_ok_rect(self):
        box, _img = self.qr_rect()
        return pygame.Rect(box.centerx - 92, box.bottom - 30, 88, 24)

    def qr_restart_rect(self):
        box, _img = self.qr_rect()
        return pygame.Rect(box.centerx + 4, box.bottom - 30, 88, 24)

    def qr_copylink_rect(self):
        box, _img = self.qr_rect()
        return pygame.Rect(box.centerx - 92, box.bottom - 58, 88, 22)

    def qr_copyaddr_rect(self):
        box, _img = self.qr_rect()
        return pygame.Rect(box.centerx + 4, box.bottom - 58, 88, 22)

    def _qr_image(self, url, scale):
        """Render the QR symbol to a surface, cached while the url and the
        chosen module size both stay the same."""
        if (self._qr_surface is not None and self._qr_url == url
                and getattr(self, "_qr_scale", None) == scale):
            return self._qr_surface
        grid = qr.encode(url) if url else [[0]]
        n = len(grid)
        quiet = 4
        side = (n + 2 * quiet) * scale
        surf = pygame.Surface((side, side))
        surf.fill((255, 255, 255))
        for r, row in enumerate(grid):
            for c, value in enumerate(row):
                if value:
                    pygame.draw.rect(surf, (0, 0, 0),
                                     ((c + quiet) * scale, (r + quiet) * scale,
                                      scale, scale))
        self._qr_surface, self._qr_url, self._qr_scale = surf, url, scale
        return surf

    def draw_qr(self):
        box, img = self.qr_rect()
        title = "Scan to join" if img is not None else "Share this game"
        body, close = xp.message_frame(self.screen, box, title, self.font,
                                       icon=getattr(self, "app_icon", None))
        self._qr_close = close
        self._qr_cap = pygame.Rect(box.x + 2, box.y + 2, box.w - 4, 24)
        y = body.y + 6
        if img is not None:
            self.screen.blit(img, (box.centerx - img.get_width() // 2, y))
            y += img.get_height() + 6
        url = self.host.share_url if self.host else ""
        label = self.font.render(url, True, COL_ACCENT_LIT)
        self.screen.blit(label, (box.centerx - label.get_width() // 2, y))
        y += self.font.get_height()
        if self.host and self.host.password:
            pw = self.font.render(const.SHARE_PASSWORD % self.host.password,
                                  True, COL_TEXT)
            self.screen.blit(pw, (box.centerx - pw.get_width() // 2, y))
        mouse = pygame.mouse.get_pos()
        link, addr = self.qr_copylink_rect(), self.qr_copyaddr_rect()
        xp.button(self.screen, link, const.SHARE_COPY_LINK, self.font,
                  hovered=link.collidepoint(mouse))
        xp.button(self.screen, addr, const.SHARE_COPY_ADDR, self.font,
                  hovered=addr.collidepoint(mouse))
        xp.button(self.screen, self.qr_ok_rect(), const.BTN_OK, self.font,
                  default=True)
        xp.button(self.screen, self.qr_restart_rect(), const.SHARE_RESTART,
                  self.font)

    def _copy_to_clipboard(self, text):
        """Put `text` on the system clipboard through pygame.scrap. Everything
        is guarded so a headless run or a platform with no clipboard degrades to
        a notice instead of raising: only the composed string and the wiring are
        relied on, never that the copy actually landed."""
        try:
            pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
            self.message = (const.DIALOG_COPY_TITLE, const.COPY_DONE)
        except Exception:  # noqa: BLE001 - dummy video driver / no clipboard
            self.message = (const.DIALOG_COPY_TITLE, const.COPY_FAIL)

    def restart_lobby(self):
        """Tear the hosted game down and go back to its setup page, so the
        lobby and the game can be started again from scratch. LAN games have
        no QR dialog; this is reached from the Game menu there."""
        self.qr_open = False
        self.stop_host()
        self.open_host_setup()

    def _draw_qr_button(self, mouse):
        """Share button: a tiny QR mark - three finder squares and a dot."""
        r = self.qr_btn
        xp.button(self.screen, r, "", self.font, hovered=r.collidepoint(mouse))
        x, y = r.x + 6, r.y + 5
        for fx, fy in ((x, y), (x + 8, y), (x, y + 8)):
            pygame.draw.rect(self.screen, COL_TEXT, (fx, fy, 6, 6), 1)
        pygame.draw.rect(self.screen, COL_TEXT, (x + 8, y + 8, 3, 3))

    def _draw_chat_button(self, mouse):
        """Chat button: a speech bubble, filled when the panel is open."""
        r = self.chat_btn
        xp.button(self.screen, r, "", self.font, hovered=r.collidepoint(mouse))
        bub = pygame.Rect(r.x + 5, r.y + 5, r.w - 10, r.h - 11)
        color = COL_ACCENT_LIT if self.chat_open else COL_TEXT
        pygame.draw.rect(self.screen, color, bub, 1)
        pygame.draw.polygon(self.screen, color,
                            [(bub.x + 3, bub.bottom), (bub.x + 3, bub.bottom + 4),
                             (bub.x + 8, bub.bottom)])

    # ============================================================ LAN client
    # "Join LAN" reaches another FligChess program over https and plays the
    # guest seat inside this one: no browser, no QR. The server holds the
    # rules; the client only paints the position it is handed and posts back
    # the move a click asks for, on the very same /api the web guest uses.

    JOIN_ROWS = (("url", "Address"), ("color", "Color"),
                 ("name", "Your name"), ("password", "Password"))
    JOIN_TEXT = ("url", "name", "password")

    def open_join(self):
        """Enter the join page; the name defaults to the one in Settings."""
        self._join_form = {"url": self._join_form.get("url", ""),
                           "color": self._join_form.get("color", "auto"),
                           "name": self._join_form.get("name")
                           or self.settings.get("client_name", ""),
                           "password": ""}
        self.state = JOIN
        self.join_index = 0
        self._join_text = None

    def join_row_rect(self, index):
        cr = self.client_rect()
        bw = min(340, cr.width - 40)
        x = cr.centerx - bw // 2
        top = cr.top + 60
        return pygame.Rect(x + 10, top + index * 34, bw - 20, 24)

    def join_box_rect(self, index):
        r = self.join_row_rect(index)
        return pygame.Rect(r.x + 96, r.y, max(40, r.w - 96), r.h)

    def join_buttons(self):
        cr = self.client_rect()
        y = cr.bottom - 46
        start = pygame.Rect(cr.centerx - 100, y, 96, 26)
        return start, start.move(104, 0)

    def join_value(self, key):
        return self._join_form[key]

    def handle_join_event(self, event):
        if event.type == pygame.TEXTINPUT and self._join_text:
            if event.text in ("\b", "\x7f"):    # some platforms carry it here
                self._join_form[self._join_text] = \
                    self._join_form[self._join_text][:-1]
            elif event.text.isprintable():
                limit = NAME_LIMIT if self._join_text != "url" else 80
                self._join_form[self._join_text] = \
                    (self._join_form[self._join_text] + event.text)[:limit]
            return
        if event.type == pygame.KEYDOWN:
            scan = getattr(event, "scancode", 0)
            if self._join_text:
                if self._erase_key(event.key, scan):
                    self._join_form[self._join_text] = \
                        self._join_form[self._join_text][:-1]
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                                   pygame.K_TAB, pygame.K_ESCAPE):
                    self._join_text = None
                elif event.key in (pygame.K_RETURN,):
                    pass
                return
            if event.key in (pygame.K_UP, pygame.K_w):
                self.join_index = (self.join_index - 1) % len(self.JOIN_ROWS)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.join_index = (self.join_index + 1) % len(self.JOIN_ROWS)
            elif self._erase_key(event.key, scan):
                # backspace focuses the highlighted field, then erases it
                key = self.JOIN_ROWS[self.join_index % len(self.JOIN_ROWS)][0]
                if key in self.JOIN_TEXT:
                    self._join_text = key
                    self._join_form[key] = self._join_form[key][:-1]
            elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                key = self.JOIN_ROWS[self.join_index % len(self.JOIN_ROWS)][0]
                if key == "color":     # the side picker answers to the arrows
                    self.cycle_join_color(1 if event.key == pygame.K_RIGHT
                                          else -1)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                key = self.JOIN_ROWS[self.join_index % len(self.JOIN_ROWS)][0]
                if key == "color":
                    self.cycle_join_color(1)
                else:
                    self._join_text = key
            elif event.key == pygame.K_ESCAPE:
                self.state = MENU
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            start, back = self.join_buttons()
            if start.collidepoint(event.pos):
                self.connect_lan()
                return
            if back.collidepoint(event.pos):
                self.state = MENU
                return
            for i, (key, _label) in enumerate(self.JOIN_ROWS):
                if self.join_box_rect(i).collidepoint(event.pos):
                    self.join_index = i
                    if key == "color":
                        self._join_text = None
                        self.cycle_join_color(1)
                    else:
                        self._join_text = key
                    return
            self._join_text = None

    def cycle_join_color(self, step=1):
        """Move the join page's side picker one notch: Auto (take whichever
        seat the host offers) through the four cross armies. The choice
        rides along on the join request as a preference - a seat of this
        app's kind is taken even if the named army is held - which the
        host's app honours while its lobby waits."""
        choices = const.JOIN_COLOR_VALUES
        cur = self._join_form.get("color", "auto")
        if cur not in choices:
            cur = "auto"
        self._join_form["color"] = choices[
            (choices.index(cur) + step) % len(choices)]

    def draw_join(self):
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        head = self.title_font.render("Join a LAN game", True, xp.CAP_MID)
        self.screen.blit(head, (cr.centerx - head.get_width() // 2, cr.y + 8))
        group = pygame.Rect(cr.centerx - 170, cr.top + 44, 340,
                            20 + len(self.JOIN_ROWS) * 34)
        xp.groupbox(self.screen, group, "Where to connect", self.small_font)
        mouse = pygame.mouse.get_pos()
        for i, (key, label) in enumerate(self.JOIN_ROWS):
            row = self.join_row_rect(i)
            box = self.join_box_rect(i)
            cap = self._fit_text(self.small_font, label, 90)
            self.screen.blit(cap, (row.x + 2, row.centery - cap.get_height() // 2))
            if key == "color":
                xp.combo(self.screen, box,
                         const.JOIN_COLOR_LABELS[
                             self._join_form.get("color", "auto")],
                         self.font, hovered=box.collidepoint(mouse))
            else:
                xp.field(self.screen, box)
                shown = self._join_form[key]
                img = self.font.render(shown, True,
                                       COL_TEXT if shown else COL_MUTED)
                self.screen.blit(img, (box.x + 4,
                                       box.centery - img.get_height() // 2))
                if self._join_text == key:
                    x = box.x + 6 + img.get_width()
                    pygame.draw.line(self.screen, COL_TEXT, (x, box.y + 3),
                                     (x, box.bottom - 4))
            if self.JOIN_ROWS[self.join_index % len(self.JOIN_ROWS)][0] == key:
                pygame.draw.rect(self.screen, (60, 60, 60), row.inflate(4, 2), 1)
        start, back = self.join_buttons()
        xp.button(self.screen, start, "Connect", self.font, default=True,
                  hovered=start.collidepoint(mouse))
        xp.button(self.screen, back, BACK, self.font,
                  hovered=back.collidepoint(mouse))
        note = self.small_font.render(
            "e.g. 192.168.1.20:8000   the address the host sees",
            True, COL_MUTED)
        self.screen.blit(note, self.bottom_text_rect(note))

    def connect_lan(self):
        """Try the address, then open the board it is serving."""
        url = self._join_form["url"].strip()
        if not url:
            self.message = ("Join LAN", "Enter the host's address first.")
            return
        name = (self._join_form["name"] or "Guest")[:NAME_LIMIT]
        self.settings["client_name"] = name
        self.settings.save()
        pw = self._join_form["password"]
        if not pw:
            # a share link can carry the password inline ("...?password=x");
            # honour it so a pasted link just works even with the field blank
            probe = url if "://" in url else "https://" + url
            query = urllib.parse.urlsplit(probe).query
            pw = urllib.parse.parse_qs(query).get("password", [""])[0]
        self.client = Client(url, pw, name)
        if not self.client.login():
            self.client = None
            self.message = ("Join LAN",
                            "Could not join. Check the address, the password,"
                            " and that a game is open.")
            return
        # ask for the seat up front, with our name and the side we picked on
        # the form; the host's app honours the ask while its lobby waits.
        # The pick may be any of the four cross armies, not just a colour.
        want = self._join_form.get("color", "auto")
        info = self.client.join(name, want if want in const.ARMY_IDS else None)
        if not info.get("ok") and not info.get("spectate"):
            self.client = None
            self.message = ("Join LAN",
                            "Could not join: that game already has a player.")
            return
        self.client_name = name
        self.lan_client = True
        self.hosting = False
        self.client_is_viewer = False
        self.chat_messages = []
        self.chat_open = False
        self._client_since = 0
        self._client_dests = {}
        self.selected = None
        self.selected_moves = []
        self._selname = None
        self.game = ChessGame()
        self.four = False              # the host's first snapshot re-shapes this
        self.seats4 = {}
        self.client_army = None
        self.variant = variants.create(self.settings["variant"])
        self.bot = self.white_bot = None
        self._review = []              # filled from the host's `plies` on poll
        self.view_ply = None
        self.state = GAME
        self._poll_client(1.0)          # grab the first position at once
        # sit on your own side of the board, exactly like the web page does
        self.flipped = self.client_color == BLACK

    def _leave_client(self):
        if self.client is not None and not self.client_is_viewer:
            self.client.leave()      # hand the seat back for the next comer
        self.lan_client = False
        self.client = None
        # the cross board leaves with the connection: any later game is
        # rebuilt from that host's own snapshots, in whatever shape it is
        self.four = False
        self.seats4 = {}
        self.client_army = None
        self._viewer = None
        self._fog = frozenset()
        self._tint = {}
        self.chat_messages = []
        self.chat_open = False
        self._chat_focus = False
        self.players = self.settings["players"]

    def _poll_client(self, dt):
        self._client_poll_t -= dt
        if self._client_poll_t > 0:
            return
        self._client_poll_t = 0.3
        try:
            snap = self.client.state(self._client_since)
        except Exception:  # noqa: BLE001 - the host vanished; say so and stop
            self.message = ("Join LAN", "Lost contact with the host.")
            self._leave_client()
            return
        self.apply_client_snapshot(snap)

    def apply_client_snapshot(self, snap):
        if not snap:
            return
        if snap.get("four"):
            self._apply_client4(snap)
            return
        if "rows" not in snap:
            return
        # the host is serving a plain board again (a fresh game in another
        # shape): drop the cross state before rebuilding the two-player one
        self.four = False
        self.seats4 = {}
        self.client_army = None
        self.game = ChessGame(Board.from_fen(_rows_to_fen(snap["rows"],
                                                          snap["turn"])))
        self.client_is_viewer = snap.get("role") == "viewer"
        self.client_color = WHITE if snap.get("guest_color") == "w" else BLACK
        self.guest_color = self.client_color
        # a spectator watches the whole board from nowhere in particular; only
        # a seated peer looks through their own side's eyes
        self._viewer = None if self.client_is_viewer else self.client_color
        if self.client_is_viewer:
            # the server blanked the pieces no army sees and sent the union
            # fog (to veil) plus the per-tile tint (to colour by team)
            self._fog = frozenset(filter(None, (_parse_square(n)
                                                for n in snap.get("spec_fog", []))))
            self._tint = {}
            for k, v in (snap.get("tint") or {}).items():
                sqp = _parse_square(k)
                if sqp:
                    self._tint[sqp] = v
        else:
            self._fog = frozenset(filter(None, (_parse_square(n)
                                                for n in snap.get("fog", []))))
            self._tint = {}
        last = snap.get("last") or []
        if len(last) == 2:
            a, b = _parse_square(last[0]), _parse_square(last[1])
            self.last_move = Move(a, b) if a and b else None
        else:
            self.last_move = None
        # a spectator is read-only: the server already refuses their moves, so
        # give them no highlights to click and treat none of the board as ours
        self._client_dests = ({} if self.client_is_viewer
                              else (snap.get("dests", {}) or {}))
        self.history = [{"san": s, "captured": None,
                         "mover": "w" if i % 2 == 0 else "b"}
                        for i, s in enumerate(snap.get("moves", []))]
        for message in snap.get("chat", []):
            self.note_chat(message)
            self._client_since = max(self._client_since, message.get("seq", 0))
        self._client_status = snap.get("result") or _client_status_line(
            snap, self.client_color)
        if self.client_is_viewer and not snap.get("result"):
            self._client_status = "Spectating"
        self.scroll_y = 0
        # keep the review history the host sent, so Prev/Next work on a LAN
        # client too; a shorter (or brand new) game drops us back to live
        plies = snap.get("plies")
        if isinstance(plies, list) and plies:
            self._review = plies
            if self.view_ply is not None and self.view_ply >= len(plies) - 1:
                self.view_ply = None
        # a new poll may invalidate a selection the host has already answered
        sel_name = sq_name(self.selected) if self.selected else None
        if sel_name and sel_name not in self._client_dests:
            self.selected = None
            self.selected_moves = []
            self._selname = None

    def handle_client_click(self, pos):
        """Pick one of the client's pieces, then one of its destinations; the
        destination is posted to the host, which plays and republishes."""
        if self.client_is_viewer:
            return                       # a spectator never posts a move
        sq = self.px_to_square(pos)
        if sq is None:
            return
        name = sq_name(sq)
        dests = self._client_dests or {}
        if self.selected and name in (dests.get(self._selname) or []):
            try:
                self.client.move(self._selname, name)
            except Exception:  # noqa: BLE001 - drop quietly, the poll retries
                pass
            self.selected = None
            self.selected_moves = []
            self._selname = None
            self.sfx.play("move")
            return
        if name in dests:
            self.selected = sq
            self._selname = name
            self.selected_moves = [Move(sq, _parse_square(d))
                                  for d in dests[name] if _parse_square(d)]
            self.sfx.play("select")
        else:
            self.selected = None
            self.selected_moves = []
            self._selname = None

    def _apply_client4(self, snap):
        """Rebuild the cross position a host is serving and paint it from
        this app's own seat. The server overlays the poller's private view
        (legal destinations, fog, move right) onto its snapshot copy, so
        what shows here is exactly what the army it holds is allowed to
        see; a spectator gets the whole board and nothing to click."""
        board = _board4.Board4()
        for y, row in enumerate(snap.get("cells") or []):
            for x, code in enumerate(row):
                if code in (".", const.CROSS_OFF) or not _cross_playable((x, y)):
                    continue
                board.set((x, y), code)
        board.turn = snap.get("turn") or _A_WHITE
        # an army with no pieces left on the published board has been
        # eliminated at the host; the chips dim those, and check reads them
        board.out = {a for a in _ARMY_IDS if not list(board.pieces_of(a))}
        self.game = Game4(board=board)
        self.four = True
        self.client_is_viewer = snap.get("role") == "viewer"
        own = None if self.client_is_viewer else snap.get("guest_color")
        self.client_army = own
        self.client_color = WHITE if own == _A_WHITE else BLACK
        self.guest_color = self.client_color
        # a spectator watches the whole cross from nowhere in particular; a
        # seated peer looks through their own army's eyes, fog and all
        self._viewer = own
        if own is None:
            # a spectator: the server blanked the pieces no army sees and sent
            # the union fog (to veil) plus the per-tile tint (to colour by team)
            self._fog = frozenset(filter(None, (_parse_cross(n)
                                                for n in snap.get("spec_fog", []))))
            self._tint = {}
            for k, v in (snap.get("tint") or {}).items():
                sqp = _parse_cross(k)
                if sqp:
                    self._tint[sqp] = v
        else:
            self._fog = frozenset(filter(None, (_parse_cross(n)
                                                for n in snap.get("fog", []))))
            self._tint = {}
        last = snap.get("last") or []
        if len(last) == 2:
            a, b = _parse_cross(last[0]), _parse_cross(last[1])
            self.last_move = Move(a, b) if a and b else None
        else:
            self.last_move = None
        self._client_dests = {} if own is None else (snap.get("dests", {}) or {})
        # names come from the per-seat views the host published; this app
        # only ever plays its own army, the rest are remote players
        views = snap.get("seat_views") or {}
        self.seats4 = {a: {"type": HUMAN if a == own else REMOTE,
                           "name": (self.client_name if a == own else
                                    (views.get(a) or {}).get("guest_name")
                                    or "Guest")}
                       for a in _ARMY_IDS}
        self.history = [{"san": s, "captured": None, "mover": None}
                        for s in snap.get("moves", [])]
        for message in snap.get("chat", []):
            self.note_chat(message)
            self._client_since = max(self._client_since, message.get("seq", 0))
        self._client_status = snap.get("result") or _client_status_line4(
            snap, own)
        if self.client_is_viewer and not snap.get("result"):
            self._client_status = "Spectating"
        self.scroll_y = 0
        # the host's whole ply history arrives in `plies` (LAN peers keep it,
        # the web handler strips it only for browser players), so Prev/Next
        # scrub the cross too; cross entries carry "cells" grids
        plies = snap.get("plies")
        if isinstance(plies, list) and plies:
            self._review = plies
            if self.view_ply is not None and self.view_ply >= len(plies) - 1:
                self.view_ply = None
        # a new poll may invalidate a selection the host has already answered
        sel_name = _cross_sq_name(self.selected) if self.selected else None
        if sel_name and sel_name not in self._client_dests:
            self.selected = None
            self.selected_moves = []
            self._selname = None

    def handle_client_click4(self, pos):
        """The two-tap flow of handle_client_click on the cross board: tap a
        piece the server listed as movable, then one of its destinations.
        The move goes over the wire in cross coordinates and the host plays
        it for this seat's army on its turn; a spectator posts nothing."""
        if self.client_is_viewer:
            return
        sq = self.px_to_cross(pos)
        if sq is None:
            return
        name = _cross_sq_name(sq)
        dests = self._client_dests or {}
        if self.selected and name in (dests.get(self._selname) or []):
            try:
                self.client.move(self._selname, name)
            except Exception:  # noqa: BLE001 - drop quietly, the poll retries
                pass
            self.selected = None
            self.selected_moves = []
            self._selname = None
            self.sfx.play("move")
            return
        if name in dests:
            self.selected = sq
            self._selname = name
            self.selected_moves = [Move(sq, _parse_cross(d))
                                   for d in dests[name] if _parse_cross(d)]
            self.sfx.play("select")
        else:
            self.selected = None
            self.selected_moves = []
            self._selname = None

    # ---------------------------------------------------------- player setup
    # The second setup screen, and the last thing between Play and a game: one
    # column per side, each with a dropdown for who plays it, one per option of
    # the engine sitting there, and a field for the name to show. A swap button
    # trades the two sides over, and nothing starts until this page says so.

    PLAYER_ROWS_H = 24          # height of one control on this page
    PLAYER_GAP = 7              # space between the controls of a column
    PLAYER_LABEL_W = 92         # room for a bot option label left of a control
    DROP_ITEM_H = 22            # one line of a popped-up dropdown list
    PLAYER_BUTTONS_H = 46       # bottom strip: Start and Back, clear of the hint

    def open_players(self):
        """Enter the player page with nothing popped up or being typed."""
        self.state = PLAYERS
        self.players_index = 0
        self._drop = self._text_focus = None

    def leave_player_page(self):
        """Back to the rules page, which re-opens on the rules actually set."""
        self.state = VARIANT
        self.variant_index = variants.VARIANTS.index(self.settings["variant"])
        self._drop = self._text_focus = None

    def player_rows(self, seat):
        """Controls a column shows, top to bottom: who plays the side, which
        engine and what it is set to when that is a computer, then the name.

        A four-player cross seat offers player, engine and name: the engine
        dropdown lists only the gate-allowed engines for this cross game, since
        an AI army must be backed by a real engine now the built-in mover is
        gone. An engine's own options stay on the two-player page, where seats
        carry a config dict of their own."""
        if self.offline_players == 4:
            rows = [("type", "")]
            if (self.offline_seats.get(seat) or {}).get("type") == AI:
                rows.append(("bot", ""))
            rows.append(("name", ""))
            return rows
        s = self.players[seat]
        rows = [("type", "")]
        if s["type"] == AI:
            rows.append(("bot", ""))
            cls = self.bots.get(s["bot"])
            rows += [("opt", key) for key in (cls.CONFIG if cls else {})]
        rows.append(("name", ""))
        return rows

    def player_focus(self):
        """Every control on the page as (seat, row), the first column first."""
        return [(seat, i) for seat in self._seat_ids()
                for i in range(len(self.player_rows(seat)))]

    def player_at(self, index):
        """The control the focus points at, kept in range when choosing a
        player type adds rows to a column or takes them away."""
        focus = self.player_focus()
        return focus[index % len(focus)]

    def player_raw(self, seat, kind, key):
        """The value a control holds, rather than the text it shows."""
        s = self._name_store()[seat]
        if kind == "type":
            return s["type"]
        if kind == "bot":
            return s.get("bot")
        if kind == "opt":
            return s.get("config", {}).get(key)
        return s["name"]

    def player_value(self, seat, kind, key):
        """The text a control shows; an empty name says what will be used."""
        raw = self.player_raw(seat, kind, key)
        if kind == "type":
            return TYPE_LABELS[raw]
        if kind == "opt":
            return str(raw)
        if kind == "bot":
            return raw or "none installed"
        if self.offline_players == 4:
            return raw or const.ARMY_LABELS[seat]
        return raw or self.player_name(WHITE if seat == "w" else BLACK)

    def player_label(self, seat, kind, key):
        """The caption left of a control; a bot names its own options."""
        if kind == "opt":
            return self.bots[self.players[seat]["bot"]].CONFIG[key]["label"]
        return {"type": "player", "bot": "bot", "name": "name"}[kind]

    def player_choices(self, seat, kind, key):
        """The items a control's dropdown holds, as (label, value) pairs."""
        if kind == "type":
            return [(TYPE_LABELS[t], t) for t in (HUMAN, AI)]
        if kind == "bot":
            # only gate-allowed engines: an AI army needs a real engine, and an
            # empty list ("none installed") means the game refuses to start
            count = 4 if self.offline_players == 4 else 2
            return [(name, name) for name in self._bots_for_mode(
                self._mode_token(count))]
        if kind == "opt":
            spec = self.bots[self.players[seat]["bot"]].CONFIG[key]
            return [(str(v), v) for v in spec["choices"]]
        return []

    def set_player_choice(self, seat, kind, key, value):
        """Take an item from a dropdown, or stepped to with the arrows."""
        if self.offline_players == 4:
            if kind == "type":
                self.offline_seats[seat]["type"] = value
            elif kind == "bot":
                self.offline_seats[seat]["bot"] = value
            self.sfx.play("select")
            self.settings.save()
            return
        if kind == "type":
            self._set_seat_type(seat, value)
        elif kind == "bot":
            self._set_seat_bot(seat, value)
        elif kind == "opt":
            self.players[seat]["config"][key] = value
        self.sfx.play("select")
        self.settings.save()

    def cycle_player_value(self, seat, kind, key, step):
        """Left and right arrows walk a dropdown's items without opening it."""
        items = self.player_choices(seat, kind, key)
        if not items:
            return
        values = [v for _label, v in items]
        raw = self.player_raw(seat, kind, key)
        at = values.index(raw) if raw in values else 0
        self.set_player_choice(seat, kind, key,
                               values[(at + step) % len(values)])

    # ------------------------------------------------------------ its layout

    # the two top buttons share one centred row: swap (128) + gap (10) +
    # the 2/4 toggle (108); the pair's total width, so both sit symmetric
    # about the middle instead of the toggle hanging off to the right
    TOP_BUTTONS_W = 128 + 10 + 108

    def swap_rect(self):
        """The swap-colors button: top of the page. A two-colour game pairs
        it with the 2 / 4 toggle and centres the pair over the middle; at
        four armies nothing swaps, so it only holds the row the toggle
        drops into and keeps the column metrics anchored."""
        cr = self.client_rect()
        w = 128
        if self.offline_players == 4:
            x = cr.centerx - w // 2
        else:
            x = cr.centerx - self.TOP_BUTTONS_W // 2
        return pygame.Rect(x, cr.top + 34, w, 24)

    def player_page_metrics(self):
        """(top, room, pitch, row height) of the two columns.

        The columns start under the swap button and its note, may be as tall
        as the space down to the Start/Back strip allows, and share one row
        stride. A window too short for the roomy stride tightens the rows and
        shrinks them with it, so a column never spills out of its box."""
        cr = self.client_rect()
        top = self.swap_rect().bottom + 20
        room = max(80, cr.bottom - self.PLAYER_BUTTONS_H - top - 8)
        rows = max(len(self.player_rows(seat)) for seat in SEATS)
        pitch = min(self.PLAYER_ROWS_H + self.PLAYER_GAP,
                    (room - 26) // max(1, rows))     # 26: caption + edge
        pitch = max(20, pitch)
        return top, room, pitch, min(self.PLAYER_ROWS_H, pitch - 2)

    def player_columns(self):
        """The two group boxes side by side, white's first, no taller than the
        longest column needs."""
        cr = self.client_rect()
        gap = 14
        w = max(140, (cr.width - gap * 3) // 2)
        top, room, pitch, row_h = self.player_page_metrics()
        rows = max(len(self.player_rows(seat)) for seat in SEATS)
        need = 18 + (rows - 1) * pitch + row_h + 8
        left = pygame.Rect(cr.x + gap, top, w, min(room, need))
        return left, left.move(w + gap, 0)

    def player_row_rect(self, seat, index):
        """One full-width row strip inside a column (or a cross army's panel)."""
        col = self.seat_column(seat)
        if self.offline_players == 4:
            pitch, row_h = self._panel_pitch(col, len(self.player_rows(seat)))
        else:
            _top, _room, pitch, row_h = self.player_page_metrics()
        y = col.y + 18 + index * pitch
        return pygame.Rect(col.x + 8, y, col.w - 16, row_h)

    def player_box_rect(self, seat, index):
        """The clickable control of a row; its caption sits left of it."""
        r = self.player_row_rect(seat, index)
        return pygame.Rect(r.x + self.PLAYER_LABEL_W, r.y,
                           max(36, r.w - self.PLAYER_LABEL_W), r.h)

    def _seat_ids(self):
        """The seats the players page edits: the two colours for a two-army
        game, all four cross armies once the offline toggle is on four."""
        return list(_ARMY_IDS) if self.offline_players == 4 else list(SEATS)

    def _name_store(self):
        """The dict a control reads and writes: the four cross seats once the
        page is showing them, otherwise the two colour seats."""
        return self.offline_seats if self.offline_players == 4 else self.players

    def _default_offline_seats(self):
        """Fresh cross seats for the offline page: white and black carry over
        whatever the two colour seats already are (an engine collapses to a
        Computer, and keeps its engine only when that engine will play a cross
        board - else it takes the default engine for the mode), red and blue
        start as Computer on the default engine. Every one can then be flipped
        to Human for hot-seat play."""
        mode = self._mode_token(4, "offline")
        default = self._default_engine(mode)

        def seed(color):
            p = self.players.get(color) if hasattr(self, "players") else None
            p = p or {}
            ptype = p.get("type", HUMAN)
            ptype = HUMAN if ptype == HUMAN else AI
            bot = p.get("bot")
            keep = (ptype == AI and bot in self.bots
                    and self._bot_allows(bot, mode))
            bot = bot if keep else (default if ptype == AI else None)
            return {"type": ptype, "bot": bot,
                    "name": p.get("name", "") or const.ARMY_LABELS[color]}
        return {"w": seed("w"), "b": seed("b"),
                "r": {"type": AI, "bot": default, "name": "Computer"},
                "bl": {"type": AI, "bot": default, "name": "Computer"}}

    def four_columns(self):
        """A 2x2 grid of army panels for the offline four-player page: white
        and black across the top, red and blue below, filling the space from
        under the mode toggle down to the Start/Back strip."""
        cr = self.client_rect()
        gap = 14
        w = max(120, (cr.width - gap * 3) // 2)
        top = self.players_mode_rect().bottom + 20
        area = max(90, cr.bottom - self.PLAYER_BUTTONS_H - top - 6)
        h = max(64, (area - gap) // 2)
        return {army: pygame.Rect(cr.x + gap + (i % 2) * (w + gap),
                                  top + (i // 2) * (h + gap), w, h)
                for i, army in enumerate(_ARMY_IDS)}

    def seat_column(self, seat):
        """The panel rect a seat's controls are laid out inside."""
        if self.offline_players == 4:
            return self.four_columns()[seat]
        return dict(zip(SEATS, self.player_columns()))[seat]

    def _panel_pitch(self, panel, rows):
        """(stride, row height) that fits `rows` controls inside a cross panel,
        tightening so a short window never spills a column out of its box."""
        room = max(24, panel.height - 26)          # 26: caption + edge
        pitch = min(self.PLAYER_ROWS_H + self.PLAYER_GAP, room // max(1, rows))
        pitch = max(20, pitch)
        return pitch, min(self.PLAYER_ROWS_H, pitch - 2)

    def player_buttons(self):
        """Start and Back, along the bottom of the page - high enough to leave
        the hint line under them readable."""
        cr = self.client_rect()
        y = cr.bottom - self.PLAYER_BUTTONS_H
        start = pygame.Rect(cr.centerx - 100, y, 96, 26)
        return start, start.move(104, 0)

    def players_mode_rect(self):
        """The 2 / 4 players toggle. Right of the swap button, the pair
        centred as one row, in a two-colour game; alone on the centre line
        once four armies are shown (there is nothing to swap across a
        free-for-all)."""
        swap = self.swap_rect()
        w = 108
        if self.offline_players == 4:
            cr = self.client_rect()
            return pygame.Rect(cr.centerx - w // 2, swap.y, w, swap.height)
        return pygame.Rect(swap.right + 10, swap.y, w, swap.height)

    def toggle_offline_players(self):
        """Flip an offline game between two and four armies. Switching up
        refreshes white and black from the two colour seats just edited, so
        the grid opens on the setup the player meant; red and blue keep
        whatever they were set to."""
        self.offline_players = 4 if self.offline_players == 2 else 2
        if self.offline_players == 4:
            fresh = self._default_offline_seats()
            self.offline_seats["w"] = fresh["w"]
            self.offline_seats["b"] = fresh["b"]
        self.sfx.play("select")

    def seats_from_offline(self):
        """The four-army seats an offline game plays, exactly as the cross
        page set them: any mix of humans (hot-seat) and Computers, each
        Computer named a gate-allowed engine. A blank name falls back to the
        army's own colour word, and an engine that is no longer installed (or
        will not play this mode) simply takes the default engine."""
        mode = self._mode_token(4, "offline")
        default = self._default_engine(mode)
        seats = {}
        for army in _ARMY_IDS:
            s = self.offline_seats.get(army, {})
            stype = s.get("type", AI)
            bot = s.get("bot")
            if stype == AI:
                bot = (bot if bot in self.bots and self._bot_allows(bot, mode)
                       else default)
            else:
                bot = None
            seats[army] = {"type": stype,
                           "bot": bot,
                           "name": s.get("name") or const.ARMY_LABELS[army]}
        return seats

    def offline_viewer(self, seats):
        """Whose eyes an offline cross game shows: the lone human's army, or
        none (the whole board) when there is more than one human to pass the
        device between, or none at all when every seat is an engine."""
        humans = [a for a in _ARMY_IDS if seats[a]["type"] == HUMAN]
        return humans[0] if len(humans) == 1 else None

    # ------------------------------------------------------------- dropdowns

    def open_drop(self, seat, index):
        """Pop a control's list open, resting on the item it is set to."""
        kind, key = self.player_rows(seat)[index]
        items = self.player_choices(seat, kind, key)
        if not items:
            return False
        values = [v for _label, v in items]
        raw = self.player_raw(seat, kind, key)
        self._drop = [seat, index,
                      values.index(raw) if raw in values else 0]
        return True

    def drop_items(self):
        """The open list's (item, rect) pairs; nothing while every list is shut."""
        if self._drop is None:
            return []
        seat, index, _cursor = self._drop
        rows = self.player_rows(seat)
        if index >= len(rows):          # the column shrank under the list
            self._drop = None
            return []
        kind, key = rows[index]
        box = self.player_box_rect(seat, index)
        items = self.player_choices(seat, kind, key)
        height = len(items) * self.DROP_ITEM_H
        top = box.bottom + 1
        if top + height > self.client_rect().bottom - self.PLAYER_BUTTONS_H:
            top = box.y - height - 1    # no room below: open upwards
        return [(item, pygame.Rect(box.x, top + i * self.DROP_ITEM_H, box.w,
                                   self.DROP_ITEM_H))
                for i, item in enumerate(items)]

    def move_drop(self, step):
        """Arrows inside an open list move its highlighted item."""
        items = self.drop_items()
        if items:
            self._drop[2] = (self._drop[2] + step) % len(items)

    def commit_drop(self):
        """Take the highlighted item of the open list and shut it."""
        items = self.drop_items()
        if not items:
            return
        seat, index, cursor = self._drop
        kind, key = self.player_rows(seat)[index]
        self._drop = None
        self.set_player_choice(seat, kind, key,
                               items[min(cursor, len(items) - 1)][0][1])

    def start_picked_game(self):
        """Start: the players are settled, so remember each engine's option
        values as this page has them and play. Every computer seat must be
        backed by an installed engine allowed to play this mode; a seat that
        cannot be - no engine at all, or none for the variant - stops the
        start with the reason rather than leaning on a built-in mover."""
        self.settings.save()
        self.sfx.play("click")
        self._drop = self._text_focus = None
        if self.offline_players == 4:
            seats = self.seats_from_offline()
            if any(s["type"] == AI and not s.get("bot")
                   for s in seats.values()):
                self.message = (const.NO_ENGINE_TITLE, const.NO_ENGINE_MSG)
                return
            self.start_game4(seats, self.offline_viewer(seats))
            return
        self.four = False          # a two-side start must leave the cross path
        mode = self._mode_token(2)
        for seat in SEATS:
            if self.players[seat]["type"] != AI:
                continue
            name = self.players[seat]["bot"]
            if name not in self.bots:
                # a computer seat must name an engine that is really installed
                self.message = ("No bots installed",
                                "Put a bot script into the bots/ folder,\n"
                                "or set that player to Human.")
                return
            if not self._bot_allows(name, mode):
                # installed, but this variant will not run it: seat the engine
                # that does, and only a mode with no allowed engine at all
                # can leave the seat unbacked enough to stop the start
                fallback = self._default_engine(mode)
                if fallback is None:
                    self.message = (const.NO_ENGINE_TITLE, const.NO_ENGINE_MSG)
                    return
                self._set_seat_bot(seat, fallback)
        for seat in SEATS:
            name = self._seat_bot(seat)
            if name:
                self.settings["bot_config"][name] = dict(
                    self.players[seat]["config"])
        self.start_game()

    # ---------------------------------------------------------------- events

    @staticmethod
    def _erase_key(key, scancode=0):
        """True when a KEYDOWN should delete one character backwards.
        Accepts the Backspace and Delete keycodes (laptops share one key)
        and either key's raw SDL scancode (42 backspace, 76 delete): some
        layouts and IMEs report the press only by scancode, and typing a
        name must never hinge on which of the three the platform chose."""
        return key in (pygame.K_BACKSPACE, pygame.K_DELETE) \
            or scancode in (42, 76)

    def type_player_name(self, text):
        """Keys typed into a name field, capped at the length that gets saved."""
        s = self._name_store()[self._text_focus[0]]
        s["name"] = (s["name"] + text)[:NAME_LIMIT]

    def erase_player_name(self):
        s = self._name_store()[self._text_focus[0]]
        s["name"] = s["name"][:-1]

    def handle_player_event(self, event):
        if event.type == pygame.TEXTINPUT and self._text_focus:
            if event.text in ("\b", "\x7f"):   # some platforms carry it here
                self.erase_player_name()
            elif event.text.isprintable():
                self.type_player_name(event.text)
            return
        if event.type == pygame.KEYDOWN:
            self.handle_player_key(event.key,
                                   getattr(event, "scancode", 0))
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.handle_player_click(event.pos)

    def handle_player_key(self, key, scancode=0):
        seat, index = self.player_at(self.players_index)
        kind, opt = self.player_rows(seat)[index]
        if self._text_focus:            # a field owns the keyboard whole
            if self._erase_key(key, scancode):
                self.erase_player_name()
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB,
                         pygame.K_ESCAPE):
                self._text_focus = None      # leave the field, keep the name
            return
        if self._drop is not None:      # arrows drive the open list instead
            if key in (pygame.K_UP, pygame.K_w):
                self.move_drop(-1)
            elif key in (pygame.K_DOWN, pygame.K_s):
                self.move_drop(1)
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.commit_drop()
            elif key == pygame.K_ESCAPE:
                self._drop = None
            return
        if key == pygame.K_ESCAPE:
            self.leave_player_page()    # Back
        elif key in (pygame.K_UP, pygame.K_w):
            self.players_index = (self.players_index - 1) % len(self.player_focus())
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.players_index = (self.players_index + 1) % len(self.player_focus())
        elif key in (pygame.K_LEFT, pygame.K_a):
            self.cycle_player_value(seat, kind, opt, -1)
        elif key in (pygame.K_RIGHT, pygame.K_d):
            self.cycle_player_value(seat, kind, opt, 1)
        elif key == pygame.K_SPACE:
            if kind == "name":
                self._text_focus = (seat, index)
            else:
                self.open_drop(seat, index)
        elif self._erase_key(key, scancode):
            # backspace into a highlighted name row edits it on the spot:
            # focus the field, then eat one character
            if kind == "name":
                self._text_focus = (seat, index)
                self.erase_player_name()
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if kind == "name":
                self._text_focus = (seat, index)
            else:
                self.start_picked_game()

    def handle_player_click(self, pos):
        for i, (_item, rect) in enumerate(self.drop_items()):
            if rect.collidepoint(pos):
                self._drop[2] = i       # the clicked item is the chosen one
                self.commit_drop()
                return
        if self.offline_players == 2 and self.swap_rect().collidepoint(pos):
            self._swap_colors()
            self.sfx.play("click")
            self.settings.save()
            return
        if self.players_mode_rect().collidepoint(pos):
            self.toggle_offline_players()
            return
        start, back = self.player_buttons()
        if start.collidepoint(pos):
            self.start_picked_game()
            return
        if back.collidepoint(pos):
            self.leave_player_page()
            return
        focus = self.player_focus()
        for seat in self._seat_ids():
            for index in range(len(self.player_rows(seat))):
                if not self.player_box_rect(seat, index).collidepoint(pos):
                    continue
                self.players_index = focus.index((seat, index))
                kind, _key = self.player_rows(seat)[index]
                if kind == "name":
                    self._text_focus = (seat, index)
                    self._drop = None
                elif self._drop and self._drop[:2] == [seat, index]:
                    self._drop = None   # the same control again shuts its list
                else:
                    self._text_focus = None
                    self.open_drop(seat, index)
                return
        # an empty spot lets go of both the field and any open list
        self._drop = self._text_focus = None

    # ------------------------------------------------------------------ draw

    def draw_players(self):
        """Who plays each side. Two colour columns under a centred swap button
        for a two-army game; a 2x2 grid of army panels (no swap) for four - an
        XP property page with a wizard's job to do."""
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        head = self.title_font.render("Who plays each side?", True, xp.CAP_MID)
        self.screen.blit(head, (cr.centerx - head.get_width() // 2, cr.y + 8))
        mouse = pygame.mouse.get_pos()
        four = self.offline_players == 4
        swap = self.swap_rect()
        if not four:
            xp.button(self.screen, swap, "Swap colors", self.font,
                      hovered=swap.collidepoint(mouse))
        mode = self.players_mode_rect()
        xp.button(self.screen, mode,
                  const.OFFLINE_PLAYERS_BUTTON % self.offline_players,
                  self.font, hovered=mode.collidepoint(mouse))
        mode_text = (const.FOUR_MODE_LABEL if four
                     else MODE_LABELS[self._mode_key()])
        note = self.small_font.render(mode_text, True, COL_ACCENT_LIT)
        note_y = (mode.bottom + 4) if four else (swap.bottom + 4)
        self.screen.blit(note, (cr.centerx - note.get_width() // 2, note_y))
        for seat in self._seat_ids():
            col = self.seat_column(seat)
            title = const.ARMY_LABELS[seat] if four else (
                "White" if seat == "w" else "Black")
            xp.groupbox(self.screen, col, title, self.small_font)
            for index, (kind, key) in enumerate(self.player_rows(seat)):
                # hover never steals the keyboard focus; see draw_host
                self.draw_player_control(seat, index, kind, key, mouse)
        start, back = self.player_buttons()
        xp.button(self.screen, start, "Start", self.font, default=True,
                  hovered=start.collidepoint(mouse))
        xp.button(self.screen, back, BACK, self.font,
                  hovered=back.collidepoint(mouse))
        self.draw_drop_list(mouse)
        hint = self.small_font.render(
            "arrows move, left/right change, space opens, enter starts",
            True, COL_MUTED)
        self.screen.blit(hint, self.bottom_text_rect(hint))

    def draw_player_control(self, seat, index, kind, key, mouse):
        """One row of a column: its caption, then the control it is set from."""
        row = self.player_row_rect(seat, index)
        box = self.player_box_rect(seat, index)
        caption = self._fit_text(self.small_font,
                                 self.player_label(seat, kind, key),
                                 self.PLAYER_LABEL_W - 6)
        self.screen.blit(caption, (row.x + 2,
                                   row.centery - caption.get_height() // 2))
        open_here = self._drop is not None and self._drop[:2] == [seat, index]
        if kind == "name":
            xp.field(self.screen, box)
            name = self._name_store()[seat]["name"]
            text = self._fit_text(self.font, self.player_value(seat, kind, key),
                                  box.w - 10, COL_TEXT if name else COL_MUTED)
            self.screen.blit(text, (box.x + 4,
                                    box.centery - text.get_height() // 2))
            if self._text_focus == (seat, index):
                x = box.x + 6 + text.get_width()
                pygame.draw.line(self.screen, COL_TEXT, (x, box.y + 3),
                                 (x, box.bottom - 4))
        else:
            xp.combo(self.screen, box, self.player_value(seat, kind, key),
                     self.font, hovered=box.collidepoint(mouse) or open_here)
        if self.player_at(self.players_index) == (seat, index):
            fr = row.inflate(4, 2)      # dotted focus rectangle, Win32 style
            pygame.draw.rect(self.screen, (60, 60, 60), fr, 1)
            pygame.draw.rect(self.screen, COL_BG, fr.move(1, 1), 1)

    def draw_drop_list(self, mouse):
        """A popped-up dropdown: a sunken list laid over whatever is under it."""
        items = self.drop_items()
        if not items:
            return
        xp.field(self.screen, items[0][1].union(items[-1][1]).inflate(2, 2))
        for i, ((label, _value), rect) in enumerate(items):
            if rect.collidepoint(mouse):
                self._drop[2] = i
            picked = i == self._drop[2]
            if picked:
                self.screen.fill(xp.SELECTION, rect)
            img = self.font.render(label, True,
                                   (255, 255, 255) if picked else xp.TEXT)
            self.screen.blit(img, (rect.x + 5,
                                   rect.centery - img.get_height() // 2))

    def bottom_text_rect(self, img, margin=8):
        """Rect for centered text that must stay fully on screen, clear of
        the status bar."""
        w, h = self.window
        x = (w - img.get_width()) // 2
        y = h - self.bottom_h - img.get_height() - 4
        return pygame.Rect(x, min(y, h - img.get_height() - 2),
                           img.get_width(), img.get_height())

    def draw_menu(self):
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)

        # welcome pane on the left, task buttons in the right column like a
        # classic XP setup wizard
        head = self.title_font.render("FligChess", True, xp.CAP_MID)
        self.screen.blit(head, (cr.x + 24, cr.y + 18))
        rule = cr.y + 18 + head.get_height() + 5
        pygame.draw.line(self.screen, xp.SHADOW, (cr.x + 24, rule),
                         (cr.x + 220, rule), 1)
        pygame.draw.line(self.screen, xp.HILITE, (cr.x + 24, rule + 1),
                         (cr.x + 220, rule + 1), 1)
        sub = self.small_font.render("chess app & bot", True, COL_MUTED)
        self.screen.blit(sub, (cr.x + 24, rule + 6))

        king_w = self.sprites["K"]
        king_b = self.sprites["k"]
        art_y = cr.y + min(cr.height - king_w.get_height() - 8,
                           int(cr.height * 0.38))
        self.screen.blit(king_w, (cr.x + 34, art_y))
        self.screen.blit(king_b, (cr.x + 34 + king_w.get_width() + 10,
                                  art_y))

        rects = self.menu_rects()
        etched_x = rects[0].x - 22
        if etched_x > cr.x + 260:
            pygame.draw.line(self.screen, xp.SHADOW,
                             (etched_x, cr.y + 12), (etched_x, cr.bottom - 12))
            pygame.draw.line(self.screen, xp.HILITE,
                             (etched_x + 1, cr.y + 12),
                             (etched_x + 1, cr.bottom - 12))
        mouse = pygame.mouse.get_pos()
        labels = {"Play": "Play", "Settings": "Settings...", "Quit": "Exit"}
        for i, (label, rect) in enumerate(zip(self.menu_items, rects)):
            if rect.collidepoint(mouse):
                self.menu_index = i
            xp.button(self.screen, rect, labels.get(label, label), self.font,
                      default=i == self.menu_index,
                      hovered=rect.collidepoint(mouse))
        hint = self.small_font.render(
            "arrows + enter, or click   F11 toggles fullscreen",
            True, COL_MUTED)
        self.screen.blit(hint, self.bottom_text_rect(hint))
        if self._update_avail:
            note = self.small_font.render(
                f"{self._update_avail} update available - "
                f"Help > Update bots...", True, COL_ACCENT_LIT)
            place = self.bottom_text_rect(note)
            self.screen.blit(note, place.move(0, -note.get_height() - 4))

    # -------------------------------------------------------------- settings
    # How the program looks, how it sounds, and how it connects to other
    # machines. Who plays and how strong an engine is belong to the setup
    # screens before a game, not here.

    SETTINGS_ROW_H = 20        # one option row
    SETTINGS_ROW_PITCH = 23    # row height plus its gap
    SETTINGS_GROUP_GAP = 20    # extra room above a group's caption frame

    def _open_settings(self):
        """Enter the Settings page. The stored Git token is read once here so
        drawing its masked row never hits the disk, and no field starts focused."""
        self._token_buf = bot_updater.load_settings().get("token", "")
        self._settings_text = None
        self.state = SETTINGS

    def settings_group_defs(self):
        """The groups shown on the Settings page. The developer ones appear
        only while "dev mode" is on and carry read-only diagnostics."""
        groups = [(GUI_SETTINGS, "Interface"), (SFX_SETTINGS, "Sound"),
                  (CONNECT_SETTINGS, "Connect"),
                  (const.UPDATER_SETTINGS, "Updater")]
        if self.settings.get("dev_mode"):
            groups += [(DEV_TOGGLES, "Development"),
                       (DEV_SERVER, "Server"), (DEV_CERT, "Certificate"),
                       (DEV_PATHS, "Paths"), (DEV_NET, "Network"),
                       (DEV_CLIENT, "Client")]
        return groups

    def _build_settings_rows(self):
        """Every option row, in group order, then the Close button. Rebuilt
        whenever "dev mode" flips so its rows appear and disappear."""
        rows = []
        for keys, _caption in self.settings_group_defs():
            rows += list(keys)
        return rows + ["back"]

    def settings_groups(self):
        """(first index, last index, caption) for each group's rows."""
        rows = self.settings_rows
        out = []
        for keys, caption in self.settings_group_defs():
            if keys[0] in rows and keys[-1] in rows:
                out.append((rows.index(keys[0]), rows.index(keys[-1]), caption))
        return out

    def row_text(self, key):
        """(label, value) shown on a settings row."""
        if key == "back":
            return "Back", ""
        if key == "reissue_cert":
            return "Reissue certificate", ""
        if key == "git_token":
            return "Git token", ""
        if key == "token_help":
            return "What is this?  ?", ""
        if key == "manage_bots":
            return "Manage bots...", ""
        return key.replace("_", " "), str(self.settings.display(key))

    def settings_rects(self, offset=None):
        """Row rects, shifted up by the scroll offset (current, if unset)."""
        if offset is None:
            offset = self._settings_scroll
        cr = self.client_rect()
        starts = {lo for lo, _hi, _c in self.settings_groups()}
        rects = []
        # 22px of headroom: a group's frame reaches 12px above its first row,
        # and the first group's caption must not be cut off by the menu bar
        y = cr.top + 22 - offset
        bw = min(380, cr.width - 24)
        for i in range(len(self.settings_rows)):
            if i in starts and i != 0:
                y += self.SETTINGS_GROUP_GAP   # room for the caption frame
            if self.settings_rows[i] == "back":
                # clear of the last group's bottom frame line
                y += 14
            rects.append(pygame.Rect(cr.centerx - bw // 2, y, bw,
                                     self.SETTINGS_ROW_H))
            y += self.SETTINGS_ROW_PITCH
        return rects

    def settings_max_scroll(self):
        """How far the page can scroll up: only as much as it overflows the
        client area, leaving room for the hint line at the bottom."""
        cr = self.client_rect()
        rects = self.settings_rects(0)
        if not rects:
            return 0
        return max(0, rects[-1].bottom - (cr.bottom - 24))

    def clamp_settings_scroll(self):
        self._settings_scroll = max(0, min(self._settings_scroll,
                                           self.settings_max_scroll()))

    def settings_keep_visible(self):
        """Scroll just enough so the highlighted row is on screen."""
        cr = self.client_rect()
        rects = self.settings_rects()
        if not (0 <= self.settings_index < len(rects)):
            return
        r = rects[self.settings_index]
        top, bot = cr.top + 8, cr.bottom - 24
        if r.top < top:
            self._settings_scroll -= top - r.top
        elif r.bottom > bot:
            self._settings_scroll += r.bottom - bot
        self.clamp_settings_scroll()

    def handle_settings_event(self, event):
        if event.type == pygame.TEXTINPUT and self._settings_text:
            if event.text in ("\b", "\x7f"):    # some platforms carry it here
                self.setting_erase()
            elif event.text.isprintable():
                self.setting_type(event.text)
            return
        if event.type == pygame.KEYDOWN:
            if self._settings_text:                 # a field owns the keyboard
                if self._erase_key(event.key, getattr(event, "scancode", 0)):
                    self.setting_erase()
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                                   pygame.K_TAB, pygame.K_ESCAPE):
                    self._settings_text = None
                return
            if event.key in (pygame.K_UP, pygame.K_w):
                self.settings_index = (self.settings_index - 1) % len(self.settings_rows)
                self.settings_keep_visible()
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.settings_index = (self.settings_index + 1) % len(self.settings_rows)
                self.settings_keep_visible()
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self.pick_setting(self.settings_index)
            elif self._erase_key(event.key, getattr(event, "scancode", 0)):
                # backspace on a typed row enters the field and erases it,
                # so a highlighted name box is editable without a click first
                key = self.settings_rows[self.settings_index
                                         % len(self.settings_rows)]
                if key == "git_token" or Settings.kind(key) == "text":
                    self.pick_setting(self.settings_index)   # enters edit mode
                    self.setting_erase()
            elif event.key == pygame.K_ESCAPE:
                self.state = self.settings_return
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self.settings_rects()):
                if rect.collidepoint(event.pos):
                    self.pick_setting(i)
                    return
            self._settings_text = None

    def setting_type(self, text):
        key = self._settings_text
        if key == "git_token":
            self._token_buf = ((self._token_buf or "") + text)[:64]
            bot_updater.save_token(self._token_buf)
            return
        self.settings[key] = (self.settings.get(key, "") + text)[:NAME_LIMIT]
        self.settings.save()

    def setting_erase(self):
        key = self._settings_text
        if key == "git_token":
            self._token_buf = (self._token_buf or "")[:-1]
            bot_updater.save_token(self._token_buf)
            return
        self.settings[key] = self.settings.get(key, "")[:-1]
        self.settings.save()

    def pick_setting(self, index):
        key = self.settings_rows[index]
        if key in DEV_SETTINGS:
            return                      # read-only diagnostics: nothing to pick
        if key == "back":
            self._settings_text = None
            self.settings.save()
            self.state = self.settings_return   # the menu, or the game behind it
            return
        if key == "token_help":
            self._settings_text = None
            self.message = (const.TOKEN_HELP_TITLE, const.TOKEN_HELP_TEXT)
            return
        if key == "manage_bots":
            self._settings_text = None
            self.open_bot_browser()
            return
        if key == "git_token":
            # enter / leave masked text-edit mode for the token
            if self._settings_text == key:
                self._settings_text = None
            else:
                self._token_buf = bot_updater.load_settings().get("token", "")
                self._settings_text = key
            return
        kind = Settings.kind(key)
        if kind == "action":
            self.reissue_cert()
            return
        if kind == "text":
            # enter or leave text-edit mode for this row
            self._settings_text = None if self._settings_text == key else key
            return
        self._settings_text = None
        self.settings.cycle(key)
        self.settings.save()
        if key == "sound":
            self.sfx.set_enabled(self.settings["sound"])
        if key == "volume":
            self.sfx.set_volume(self.settings["volume"])
        if key in ("sound", "volume"):
            self.sfx.play("click")     # so the change can be heard right away
        if key == "fullscreen":
            self.fullscreen = self.settings["fullscreen"]
            self.window = (self._current_display_size() if self.fullscreen
                           else DEFAULT_WINDOW)
            self.screen = self._set_mode()
        if key in ("board_theme", "coordinates", "move_panel"):
            self._layout()
        if key == "dev_mode":
            # the developer rows appear or vanish, so rebuild the list and
            # keep the cursor and scroll within the new, shorter page
            self.settings_rows = self._build_settings_rows()
            self.settings_index = min(self.settings_index,
                                      len(self.settings_rows) - 1)
            self.clamp_settings_scroll()

    def reissue_cert(self):
        """Make a brand new self-signed certificate from the settings. RSA in
        pure Python takes a couple of seconds, so this is a deliberate button
        rather than something done behind every launch."""
        try:
            info = cert.issue(bits=self.settings["cert_bits"],
                              days=self.settings["cert_days"], force=True)
            self.message = ("Certificate",
                            "Issued a new self-signed certificate.\n\n"
                            "SHA-256 fingerprint:\n%s" % info["fingerprint"])
        except Exception as exc:  # noqa: BLE001 - report, never crash the app
            self.message = ("Certificate", "Could not issue one:\n%s" % exc)
        self.sfx.play("click")

    def draw_settings(self):
        cr = self.client_rect()
        self.screen.fill(COL_BG, cr)
        self.clamp_settings_scroll()
        rects = self.settings_rects()
        mouse = pygame.mouse.get_pos()
        dev = self.dev_values() if self.settings.get("dev_mode") else {}

        # clip to the client area, minus the hint strip, so scrolled rows and
        # group frames never bleed over the menu or status bars
        old_clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(cr.x, cr.top, cr.width,
                                         max(0, cr.height - 22)))

        # one etched group box per section: looks, sounds, then connecting
        for lo, hi, caption in self.settings_groups():
            page = rects[lo].union(rects[hi]).inflate(28, 24)
            page.midtop = (cr.centerx, rects[lo].top - 12)
            xp.groupbox(self.screen, page, caption, self.small_font)

        for i, (key, rect) in enumerate(zip(self.settings_rows, rects)):
            if rect.collidepoint(mouse) and not self._settings_text:
                self.settings_index = i
            selected = i == self.settings_index
            if key in DEV_SETTINGS:
                self._draw_dev_row(rect, dev.get(key, (key, "")))
                continue
            label, value = self.row_text(key)
            if key != "back":
                lbl = self.font.render(label, True, COL_TEXT)
                self.screen.blit(lbl, (rect.x + 10, rect.centery
                                       - lbl.get_height() // 2))
            ctl_x = rect.x + int(rect.width * 0.42)
            if key == "back":
                btn = pygame.Rect(rect.right - 92, rect.y - 2, 88, 24)
                xp.button(self.screen, btn, "Close", self.font,
                          default=selected, hovered=btn.collidepoint(mouse))
                continue
            if key == "reissue_cert":
                btn = pygame.Rect(ctl_x, rect.y - 1, 150, 22)
                xp.button(self.screen, btn, "Reissue", self.font,
                          default=selected, hovered=btn.collidepoint(mouse))
            elif key == "git_token":
                # a masked field: every character shows as a bullet, so the
                # token stays private while it is being typed
                box = pygame.Rect(ctl_x, rect.y - 1,
                                  max(60, rect.right - ctl_x - 10), 22)
                xp.field(self.screen, box)
                shown = "\u2022" * len(self._token_buf or "")
                img = self.font.render(shown, True,
                                       COL_TEXT if shown else COL_MUTED)
                self.screen.blit(img, (box.x + 4, box.centery
                                       - img.get_height() // 2))
                if self._settings_text == key:
                    x = box.x + 6 + img.get_width()
                    pygame.draw.line(self.screen, COL_TEXT, (x, box.y + 3),
                                     (x, box.bottom - 4))
            elif key == "token_help":
                btn = pygame.Rect(rect.right - 34, rect.y - 1, 26, 22)
                xp.button(self.screen, btn, "?", self.font,
                          hovered=btn.collidepoint(mouse))
            elif key == "manage_bots":
                btn = pygame.Rect(ctl_x, rect.y - 1, 150, 22)
                xp.button(self.screen, btn, "Open", self.font,
                          default=selected, hovered=btn.collidepoint(mouse))
            elif Settings.kind(key) == "text":
                box = pygame.Rect(ctl_x, rect.y - 1, max(60, rect.right - ctl_x - 10), 22)
                xp.field(self.screen, box)
                shown = self.settings.get(key, "")
                img = self.font.render(shown, True,
                                       COL_TEXT if shown else COL_MUTED)
                self.screen.blit(img, (box.x + 4, box.centery
                                       - img.get_height() // 2))
                if self._settings_text == key:
                    x = box.x + 6 + img.get_width()
                    pygame.draw.line(self.screen, COL_TEXT, (x, box.y + 3),
                                     (x, box.bottom - 4))
            elif Settings.kind(key) == "bool":
                xp.checkbox(self.screen, (ctl_x + 8, rect.centery),
                            bool(self.settings[key]), "", self.font)
            else:
                cw = min(170, rect.right - ctl_x - 10)
                box = pygame.Rect(ctl_x, rect.y - 1, cw, 22)
                xp.combo(self.screen, box, value, self.font,
                         hovered=selected or box.collidepoint(mouse))
            if selected and not self._settings_text:  # dotted focus rectangle
                fr = rect.inflate(4, 2)
                pygame.draw.rect(self.screen, (60, 60, 60), fr, 1)
                pygame.draw.rect(self.screen, COL_BG, fr.move(1, 1), 1)
        self.screen.set_clip(old_clip)
        hint_text = "click / enter to change   esc to go back   wheel to scroll"
        # hovering the token row trades the generic hint for its explanation,
        # a lightweight tooltip that needs no separate popup layer
        if (0 <= self.settings_index < len(self.settings_rows)
                and self.settings_rows[self.settings_index] == "git_token"):
            hint_text = const.TOKEN_FIELD_TIP
        hint = self.small_font.render(hint_text, True, COL_MUTED)
        self.screen.blit(hint, self.bottom_text_rect(hint))

    def _draw_dev_row(self, rect, pair):
        """One read-only developer line: label at the left, its value at the
        right, trimmed with an ellipsis so long paths and fingerprints stay
        inside the frame."""
        label, value = str(pair[0]), str(pair[1])
        lbl = self.small_font.render(label, True, COL_MUTED)
        self.screen.blit(lbl, (rect.x + 10,
                               rect.centery - lbl.get_height() // 2))
        avail = max(20, rect.width - lbl.get_width() - 26)
        img = self.small_font.render(value, True, COL_ACCENT_LIT)
        while img.get_width() > avail and len(value) > 1:
            value = value[:-2]
            img = self.small_font.render(value + "\u2026", True,
                                         COL_ACCENT_LIT)
        self.screen.blit(img, (rect.right - 10 - img.get_width(),
                               rect.centery - img.get_height() // 2))

    def dev_values(self):
        """Live, read-only diagnostics for the developer section: the http(s)
        server, its certificate, the on-disk paths, the network and the far
        end of any connection. Computed fresh each time the page is drawn."""
        h = self.host
        running = h is not None
        try:
            meta = cert.load_meta() or {}
        except Exception:  # noqa: BLE001 - a corrupt meta is simply "none"
            meta = {}

        def when(ts):
            try:
                return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
            except (TypeError, ValueError):
                return "-"

        tls = cert.tls_dir()
        try:
            ip = local_ip()
        except Exception:  # noqa: BLE001 - no route is not a crash
            ip = "127.0.0.1"
        try:
            name = socket.gethostname()
        except Exception:  # noqa: BLE001
            name = "-"
        vals = {
            # the http(s) server
            "dev_server": ("server", "running" if running else "stopped"),
            "dev_url": ("url", h.url if running else "-"),
            "dev_https": ("https", ("on" if h.tls is not None else "off")
                          if running else
                          ("on" if self.settings["https"] else "off")),
            "dev_port": ("port", str(getattr(h, "port", "-")) if running else "-"),
            "dev_mode_kind": ("mode", ("lan (json)" if self.lan_mode else "web")
                              if running else "-"),
            "dev_visitors": ("visitors", str(h.visitors()) if running else "0"),
            "dev_guest": ("guest seated",
                          "yes" if (running and h.has_guest()) else "no"),
            # the certificate
            "dev_cert_state": ("certificate", "present" if meta else "none"),
            "dev_cert_bits": ("key bits", str(meta.get("bits", "-"))),
            "dev_cert_days": ("lifetime", str(meta.get("days", "-")) + " d"
                              if meta.get("days") else "-"),
            "dev_cert_issued": ("issued", when(meta.get("issued"))),
            "dev_cert_expiry": ("expires", when(meta.get("not_after"))),
            "dev_cert_hosts": ("hosts", ", ".join(meta.get("hosts", [])) or "-"),
            "dev_cert_fp": ("sha-256", (meta.get("fingerprint") or "-")[:32]),
            # files on disk
            "dev_p_config": ("config dir", str(config_dir())),
            "dev_p_settings": ("settings", str(SETTINGS_PATH)),
            "dev_p_cert": ("cert.pem", str(tls / "cert.pem")),
            "dev_p_key": ("key.pem", str(tls / "key.pem")),
            "dev_p_meta": ("meta.json", str(tls / "meta.json")),
            "dev_p_bots": ("bots dir", str(bots_dir())),
            "dev_p_app": ("app root", str(APP_ROOT)),
            # connectivity
            "dev_net_ip": ("local ip", ip),
            "dev_net_host": ("hostname", name),
            "dev_net_listen": ("listening",
                               "0.0.0.0:%s" % h.port if running else "-"),
        }
        # client: this app joined someone else's game, or is itself hosting
        cl = self.client
        if self.lan_client and cl is not None:
            vals["dev_cli_role"] = ("role", "client (joined)")
            vals["dev_cli_url"] = ("server", cl.url)
            vals["dev_cli_name"] = ("my name", self.client_name or "-")
            vals["dev_cli_color"] = ("colour", "white" if self.client_color == WHITE else "black")
            vals["dev_cli_status"] = ("status", self._client_status or "-")
        elif running:
            vals["dev_cli_role"] = ("role", "host (serving)")
            vals["dev_cli_url"] = ("server", h.url)
            vals["dev_cli_name"] = ("my name", self.player_name(self.host_color))
            vals["dev_cli_color"] = ("colour", "white" if self.host_color == WHITE else "black")
            vals["dev_cli_status"] = ("status",
                                      "over" if (self.game and self.game.result())
                                      else "playing")
        else:
            vals["dev_cli_role"] = ("role", "local (offline)")
            vals["dev_cli_url"] = ("server", "-")
            vals["dev_cli_name"] = ("my name", "-")
            vals["dev_cli_color"] = ("colour", "-")
            vals["dev_cli_status"] = ("status", "-")
        return vals

    # ------------------------------------------------------------------ game

    def handle_game_event(self, event):
        if event.type == pygame.TEXTINPUT and self._chat_focus:
            if event.text in ("\b", "\x7f"):  # some platforms carry it here
                self._chat_input = self._chat_input[:-1]
            elif event.text.isprintable():
                self._chat_input = (self._chat_input + event.text)[:200]
            return
        if event.type == pygame.KEYDOWN:
            scan = getattr(event, "scancode", 0)
            if self._chat_focus:
                if self._erase_key(event.key, scan):
                    self._chat_input = self._chat_input[:-1]
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.send_chat()
                elif event.key == pygame.K_ESCAPE:
                    self._chat_focus = False
                return
            if event.key == pygame.K_ESCAPE:
                self.state = MENU
            elif self._erase_key(event.key, scan) and self.chat_open:
                # backspace reaches into the open chat box without a click
                self._chat_focus = True
                self._chat_input = self._chat_input[:-1]
            elif event.key == pygame.K_r and not self.lan_client:
                self.start_game()
            elif (event.key == pygame.K_u and not self.anim and not self.duel()
                    and not self.lan_client):
                self._undo()
            elif event.key == pygame.K_f:
                self.toggle_flip()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.chat_open and self.handle_chat_click(event.pos):
                return
            if (self.hosting or self.lan_client) \
                    and self.chat_btn.collidepoint(event.pos):
                self.chat_open = not self.chat_open
                self._chat_focus = False
                self._layout()          # reserve/release the docked-chat column
                self.sfx.play("click")
                return
            if self.hosting and self.qr_btn.collidepoint(event.pos):
                self.qr_open = True
                self.sfx.play("click")
                return
            if self.flip_btn.collidepoint(event.pos):
                self.toggle_flip()
                return
            if self.review_available():
                if self.prev_btn.collidepoint(event.pos):
                    self.review_prev()
                    self.sfx.play("click")
                    return
                if self.next_btn.collidepoint(event.pos):
                    self.review_next()
                    self.sfx.play("click")
                    return
                if self.live_btn.collidepoint(event.pos):
                    self.review_jump_live()
                    self.sfx.play("click")
                    return
            if self.reviewing():
                return                   # a past position is view-only
            if self.four and self.lan_client:
                self.handle_client_click4(event.pos)
            elif self.four:
                self.handle_game4_click(event.pos)
            elif self.lan_client:
                self.handle_client_click(event.pos)
            else:
                self.handle_click(event.pos)

    # -------------------------------------------------------------- chat panel

    def panel_split(self, rect):
        """Split the sidebar into the docked chat (on top) and the move
        history (below). With both open, chat takes the top slice (up to 45%
        of the height, capped) and the history shrinks into the rest; with
        only one open it owns the whole column. Returns (chat_rect,
        history_rect), either of which may be None."""
        chat_want = self.chat_open
        hist_want = self.settings["move_panel"]
        if chat_want and hist_want:
            chat_h = min(240, int(rect.height * 0.45))
            chat = pygame.Rect(rect.x, rect.y, rect.w, chat_h)
            history = pygame.Rect(rect.x, chat.bottom + 4, rect.w,
                                  max(40, rect.bottom - chat.bottom - 4))
            return chat, history
        if chat_want:
            return rect, None
        return None, rect

    def _panel_region(self):
        """The full sidebar rectangle, from the divider to the bottom bar."""
        w, h = self.window
        return pygame.Rect(self.panel_x, self.status_h, w - self.panel_x,
                           max(40, h - self.status_h - self.bottom_h))

    def chat_region(self):
        """The part of the sidebar the docked chat occupies, or None closed."""
        if not self.chat_open:
            return None
        return self.panel_split(self._panel_region())[0]

    def chat_rect(self):
        return self.chat_region() or pygame.Rect(0, 0, 0, 0)

    def chat_input_rect(self):
        r = self.chat_rect()
        return pygame.Rect(r.x + 8, r.bottom - 30, max(20, r.w - 66), 22)

    def chat_send_rect(self):
        r = self.chat_rect()
        return pygame.Rect(r.right - 54, r.bottom - 30, 46, 22)

    def handle_chat_click(self, pos):
        """A click in the docked chat: the Send button or the input box. The
        panel stays docked, so a click anywhere else is not swallowed here -
        it returns False and the rest of the game UI handles it as usual."""
        r = self.chat_region()
        if r is None or not r.collidepoint(pos):
            return False
        if self.chat_send_rect().collidepoint(pos):
            self.send_chat()
        elif self.chat_input_rect().collidepoint(pos):
            self._chat_focus = True
        else:
            self._chat_focus = False
        return True

    def send_chat(self):
        """Post what is typed into the shared log. The host writes straight
        through its server; a LAN client sends it over the wire instead."""
        text = (self._chat_input or "").strip()
        if not text:
            return
        self._chat_input = ""
        if self.host is not None:
            if self.four:
                seat = self.seats4.get(self.host_army, {})
                who = seat.get("name") or "You"
                side = self.host_army
            else:
                who = self.player_name(self.host_color)
                side = "w" if self.host_color == WHITE else "b"
            msg = self.host.post_chat(who, text, color=side)
            if msg:
                self.chat_messages.append(msg)
        elif self.client is not None:
            try:
                self.client.chat(text)
            except Exception:  # noqa: BLE001 - the poll reports lost contact
                self.message = (const.CHAT_TITLE, const.CHAT_FAIL_TEXT)
                return
            # no local echo: the host relays the message back through the
            # next poll, with its sequence number, like any other line
        self.sfx.play("click")

    def note_chat(self, message):
        """Add one guest or peer message to the panel, skipping repeats."""
        if message and message not in self.chat_messages:
            self.chat_messages.append(message)

    def _note_bot_say(self, who, side, text):
        """One line an engine said through GameInfo.say(): into the chat panel
        the local player sees, and through the server when one is up so guests
        and peer apps read it in the same log as everything else."""
        text = (text or "").strip()
        if not text:
            return
        message = None
        if self.host is not None:
            try:
                message = self.host.post_chat(who, text, color=side or "")
            except Exception:  # noqa: BLE001 - a lost server must not stop play
                message = None
        self.note_chat(message or {"who": who, "text": text, "color": side})

    def _chat_pawn(self, color):
        """A small pawn sprite marking a chat line's side. All four armies -
        the plain white/black pair included - come off the cross minis, so
        every mark is the same size and drawn from the same pallet; leaning
        on the two-player minis for white and black mixed two sprite sets
        (and two apparent sizes) into one chat log. A spectator (or no
        side) gets no mark."""
        if color in ("w", "b", "r", "bl"):
            return getattr(self, "minis4", {}).get("P" + color)
        return None

    def _draw_chat_block(self, r):
        """The docked chat inside the sidebar region `r`: a title, the message
        log scrolled to the newest, then an input line and a Send button along
        the bottom. It is part of the panel, so it never floats over the board."""
        title = self.small_font.render(const.CHAT_TITLE, True, COL_TEXT)
        self.screen.blit(title, (r.x + 8, r.y + 5))
        top = r.y + 5 + title.get_height() + 4
        log = pygame.Rect(r.x + 6, top, max(20, r.w - 12),
                          max(20, (r.bottom - 34) - top))
        pygame.draw.rect(self.screen, (255, 255, 255), log)
        pygame.draw.rect(self.screen, xp.SHADOW, log, 1)
        clip = pygame.Rect(log.x + 2, log.y + 2, max(1, log.w - 4),
                           max(1, log.h - 4))
        old = self.screen.get_clip()
        self.screen.set_clip(clip)
        y = clip.bottom
        for msg in reversed(self.chat_messages):
            line = "%s: %s" % (msg["who"], msg["text"])
            img = self.small_font.render(line, True, COL_TEXT)
            y -= img.get_height() + 2
            x = clip.x + 2
            mark = self._chat_pawn(msg.get("color"))
            if mark is not None:
                self.screen.blit(mark, (x, y + (img.get_height()
                                               - mark.get_height()) // 2))
                x += mark.get_width() + 3
            self.screen.blit(img, (x, y))
            if y <= clip.top:
                break
        self.screen.set_clip(old)
        inp, snd = self.chat_input_rect(), self.chat_send_rect()
        xp.field(self.screen, inp)
        shown = self._chat_input or ("" if self._chat_focus
                                     else const.CHAT_PLACEHOLDER)
        img = self.font.render(shown, True,
                               COL_TEXT if self._chat_input else COL_MUTED)
        self.screen.blit(img, (inp.x + 4, inp.centery - img.get_height() // 2))
        mouse = pygame.mouse.get_pos()
        xp.button(self.screen, snd, const.CHAT_SEND, self.font,
                  hovered=snd.collidepoint(mouse))

    def handle_click(self, pos):
        if self.anim:                      # input is locked during animations
            return
        if self.show_over:
            for rect, action in self.over_rects():
                if rect.collidepoint(pos):
                    action()
            return
        if self.pending_promotion:
            for cell, ch in self.promo_choices:
                if cell.collidepoint(pos):
                    f, t = self.pending_promotion
                    self.pending_promotion = None
                    self._play(Move(f, t, ch.lower()))
                    return
            self.pending_promotion = None  # click outside cancels
            return

        sq = self.px_to_square(pos)
        if sq is None or self.game.result():
            return
        if self._side_bot(self.game.board.turn):
            return                   # never let clicks move a bot's pieces
        if self.hosting and self.game.board.turn == self.guest_color:
            return                   # the guest's side belongs to the browser

        # clicking a highlighted destination plays the move
        if self.selected:
            moves = [m for m in self.selected_moves if m.to_sq == sq]
            if moves:
                if len(moves) > 1 and moves[0].promotion:  # promotion choice
                    self.pending_promotion = (self.selected, sq)
                else:
                    self._play(moves[0])
                return

        piece = self.game.board.get(sq)
        if piece and _piece_color(piece) == self.game.board.turn:
            self.selected = sq
            self.selected_moves = self.game.moves_from(sq)
            self.sfx.play("select")
        else:
            self.selected = None
            self.selected_moves = []

    def scroll_panel(self, wheel_y):
        if not self.settings["move_panel"] or self.game is None:
            return
        step = (self.panel_font.get_height() + 5) * 2
        self.scroll_y = max(0, self.scroll_y - wheel_y * step)
        self.follow_latest = False

    def _over_text_img(self):
        """Result text rendered in the largest font that still fits the window.
        A duel stopped with End Game shows that plainly instead of a result."""
        max_w = self.window[0] - 60
        text = "Game ended" if self.duel_ended else self.game.status_text()
        img = None
        for font in (self.big_font, self.font, self.small_font, self.tiny_font):
            img = font.render(text, True, COL_TEXT)
            if img.get_width() <= max_w:
                return img
        return img

    def over_panel_rect(self):
        """Game-over panel, sized around its text so the text never
        overflows; stays where the user dragged it, if anywhere."""
        img = self._over_text_img()
        pw = max(240, min(self.window[0] - 20, img.get_width() + 40))
        panel = pygame.Rect(0, 0, pw, 130)
        panel.center = self.over_pos or (self.window[0] // 2,
                                         self.window[1] // 2)
        return panel

    def over_rects(self):
        """Buttons on the game-over panel, as (rect, action) pairs."""
        panel = self.over_panel_rect()
        gap, bh = 16, 36
        bw = min(130, (panel.w - 40 - gap) // 2)
        y = panel.centery + 18
        return [
            (pygame.Rect(panel.centerx - bw - gap // 2, y, bw, bh), self.start_game),
            (pygame.Rect(panel.centerx + gap // 2, y, bw, bh),
             lambda: setattr(self, "state", MENU)),
        ]

    def draw_game(self):
        w, h = self.window
        sq, status_h = self.square, self.status_h
        self.screen.fill(COL_BG, (0, self.chrome_top, w,
                                  max(1, h - self.chrome_top - self.bottom_h)))
        self.screen.blit(self.board_surface, (self.board_off_x, status_h))
        frame = self.board_surface.get_rect()
        frame.topleft = (self.board_off_x, status_h)
        xp.sunken(self.screen, frame.inflate(4, 4).move(-2, -2))

        reviewing = self.reviewing()
        entry = self._review[self.view_ply] if reviewing else None
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        animating = self.anim is not None and not reviewing
        if reviewing:
            # a past position wears its own fog and tint, not the live view's
            fog = self._review_fog(entry, _parse_square)
            tint = self._review_tint(entry, _parse_square)
        else:
            fog = self._fog
            tint = self._tint
        for fog_sq in fog:               # the tiles this view may not see
            x, y = self.square_to_px(fog_sq)
            pygame.draw.rect(overlay, COL_FOG, (x, y + status_h, sq, sq))
        self._paint_tint(overlay, tint, self.square_to_px, sq, status_h)
        if reviewing:
            # a past position: just flag the two squares of the move that
            # made it - the game is on hold, so no selection or check
            for name in (entry.get("last") or []):
                mv = _parse_square(name)
                if mv:
                    x, y = self.square_to_px(mv)
                    pygame.draw.rect(overlay, COL_LAST_MOVE,
                                     (x, y + status_h, sq, sq))
        else:
            if not animating:
                if self.last_move:
                    for mv in (self.last_move.from_sq, self.last_move.to_sq):
                        if mv in fog:
                            continue        # a move lost in fog stays lost
                        x, y = self.square_to_px(mv)
                        pygame.draw.rect(overlay, COL_LAST_MOVE,
                                         (x, y + status_h, sq, sq))
                if self.selected and self.selected not in fog:
                    x, y = self.square_to_px(self.selected)
                    pygame.draw.rect(overlay, COL_SELECTED,
                                     (x, y + status_h, sq, sq))
            if self.game.board.in_check():
                king_sq = self.game.board.find_king(self.game.board.turn)
                if king_sq:
                    x, y = self.square_to_px(king_sq)
                    pygame.draw.rect(overlay, COL_CHECK,
                                     (x, y + status_h, sq, sq))
        self.screen.blit(overlay, (0, 0))

        # pieces: a stored row set while reviewing, else the live board
        if reviewing:
            prow = entry["rows"]
            for rank in range(8):
                for file_ in range(8):
                    if (file_, rank) in fog:
                        continue        # this ply's own fog hides the piece too
                    piece = prow[7 - rank][file_]
                    if piece and piece != ".":
                        sprite = self.sprites.get(piece)
                        if sprite is None:
                            continue
                        x, y = self.square_to_px((file_, rank))
                        offset = (sq - sprite.get_width()) // 2
                        self.screen.blit(sprite,
                                         (x + offset, y + status_h + offset))
        else:
            # hiding the ones currently sliding and the ones under fog
            hidden = {to for _, _, to in self.anim["tracks"]} if animating else set()
            for rank in range(8):
                for file_ in range(8):
                    sqp = (file_, rank)
                    if sqp in hidden or not self._shown(sqp):
                        continue
                    piece = self.game.board.get(sqp)
                    if piece:
                        x, y = self.square_to_px(sqp)
                        sprite = self.sprites[piece]
                        offset = (sq - sprite.get_width()) // 2
                        self.screen.blit(sprite, (x + offset,
                                                  y + status_h + offset))

            # sliding pieces on top
            if animating:
                progress = ease_out(min(1.0, self.anim["t"] / self.anim["dur"]))
                for piece, frm, to in self.anim["tracks"]:
                    if not (self._shown(frm) or self._shown(to)):
                        continue    # it slid from one fog into another
                    fx, fy = self.square_to_px(frm)
                    tx, ty = self.square_to_px(to)
                    x = fx + (tx - fx) * progress
                    y = fy + (ty - fy) * progress
                    sprite = self.sprites[piece]
                    offset = (sq - sprite.get_width()) // 2
                    self.screen.blit(sprite, (int(x) + offset,
                                              int(y) + status_h + offset))

            for effect, x, y in self.effects:
                effect.draw(self.screen, x, y)

            # legal move markers
            if self.settings["move_hints"]:
                for move in self.selected_moves:
                    x, y = self.square_to_px(move.to_sq)
                    cx, cy = x + sq // 2, y + status_h + sq // 2
                    if self.game.board.get(move.to_sq):
                        pygame.draw.circle(self.screen, COL_DOT[:3], (cx, cy),
                                           sq // 2 - 3, width=max(2, sq // 12))
                    else:
                        pygame.draw.circle(self.screen, COL_DOT[:3], (cx, cy),
                                           max(3, sq // 7))

            if self.game.result():
                self.draw_markers()  # the game is over: show what was hidden

        self.draw_status()
        self.draw_review_strip()
        self.draw_bottom_hint()
        self.draw_turn_indicators()
        if self.settings["move_panel"] or self.chat_open:
            self.draw_panel()        # sidebar: docked chat and/or move history
        if self.pending_promotion:
            self.draw_promotion_picker()
        if self.show_over:
            self.draw_game_over()

    def draw_markers(self):
        """Reveal whatever the variant kept hidden once the game is over -
        in a minefield, the mines still armed. A spiked disc, mine-style."""
        armed = self.variant.markers(self.game.board)
        if not armed:
            return
        rad = max(3, self.square // 7)
        for sq in armed:
            if not self._shown(sq):
                continue
            x, y = self.square_to_px(sq)
            cx = x + self.square // 2
            cy = y + self.status_h + self.square // 2
            for ang in range(0, 180, 45):        # four spikes, both ways
                dx = math.cos(math.radians(ang)) * (rad + 3)
                dy = math.sin(math.radians(ang)) * (rad + 3)
                pygame.draw.line(self.screen, COL_MINE, (cx - dx, cy - dy),
                                 (cx + dx, cy + dy), 2)
            pygame.draw.circle(self.screen, COL_MINE, (cx, cy), rad)

    def draw_status(self):
        text = (self._client_status if self.lan_client and self._client_status
                else self.game.status_text())
        if self._think is not None:
            thinking = self._side_bot(self.game.board.turn)
            if thinking:
                text += f"  \u00b7  {thinking.name} thinking\u2026"
        if self.hosting and self.host is not None:
            watching = self.host.viewer_count()
            if watching:
                text += const.STATUS_WATCHING % watching
        row_top, row_h = self.chrome_top, self.status_h - self.chrome_top
        online = self.hosting or self.lan_client
        leftmost = self.chat_btn.left if online else self.flip_btn.left
        max_w = leftmost - 24
        img = self._fit_text(self.font, text, max_w)
        self.screen.blit(img, (12, row_top + (row_h - img.get_height()) // 2))
        # flip-board button: two stacked triangles, like a list-sort glyph
        mouse = pygame.mouse.get_pos()
        xp.button(self.screen, self.flip_btn, "", self.font,
                  hovered=self.flip_btn.collidepoint(mouse))
        cx, cy = self.flip_btn.center
        pygame.draw.polygon(self.screen, COL_TEXT,
                            [(cx - 4, cy - 1), (cx + 4, cy - 1),
                             (cx, cy - 6)])
        pygame.draw.polygon(self.screen, COL_TEXT,
                            [(cx - 4, cy + 1), (cx + 4, cy + 1),
                             (cx, cy + 6)])
        if self.hosting:
            self._draw_qr_button(mouse)
        if online:
            self._draw_chat_button(mouse)
        # etched separator under the status row, like a toolbar well
        y = self.status_h - 3
        pygame.draw.line(self.screen, xp.SHADOW, (6, y), (self.window[0] - 6, y))
        pygame.draw.line(self.screen, xp.HILITE,
                         (6, y + 1), (self.window[0] - 6, y + 1))

    def draw_review_strip(self):
        """The Prev / Next / Latest scrubber under the board, plus a label
        saying which ply is shown. Only appears once there is a move to walk
        back to; at the newest position it reads 'Live'."""
        if not self.review_available():
            return
        mouse = pygame.mouse.get_pos()
        live = self.review_live()
        cur = live if self.view_ply is None else self.view_ply
        xp.button(self.screen, self.prev_btn, "<", self.small_font,
                  hovered=self.prev_btn.collidepoint(mouse))
        xp.button(self.screen, self.next_btn, ">", self.small_font,
                  hovered=self.next_btn.collidepoint(mouse))
        xp.button(self.screen, self.live_btn, "Live", self.small_font,
                  hovered=self.live_btn.collidepoint(mouse))
        label = ("Live" if cur == live else "Move %d / %d" % (cur, live))
        img = self.small_font.render(label, True, COL_TEXT if self.reviewing()
                                      else COL_MUTED)
        x = self.live_btn.right + 8
        if x + img.get_width() > self.window[0] - 4:
            x = self.prev_btn.left
            y = self.prev_btn.top - img.get_height() - 2
        else:
            y = self.prev_btn.centery - img.get_height() // 2
        self.screen.blit(img, (x, y))

    def bottom_hint_rect(self):
        """Where the key tips go: under the board, centered, never off-screen."""
        w = self.window[0]
        full = ("[R] new   [U] undo   [F] flip   [F11] fullscreen   "
                "[Esc] menu")
        hint = self.small_font.render(full, True, COL_MUTED)
        if hint.get_width() > w - 8:
            hint = self.tiny_font.render(
                "[R]new [U]undo [F]flip [F11]full [Esc]menu",
                True, COL_MUTED)
        board_cx = self.board_off_x + self.margin + self.board_px // 2
        x = board_cx - hint.get_width() // 2
        x = max(4, min(x, w - hint.get_width() - 4))
        y = self.window[1] - self.bottom_h + (self.bottom_h - hint.get_height()) // 2
        return hint, pygame.Rect(x, y, hint.get_width(), hint.get_height())

    def draw_bottom_hint(self):
        """Key tips below the board, centered on it and always on screen."""
        hint, rect = self.bottom_hint_rect()
        self.screen.blit(hint, rect)

    # ------------------------------------------------- turn indicator pawns

    @staticmethod
    def _silhouette(sprite, color):
        """Solid one-color copy of a sprite, keeping its alpha outline."""
        sil = sprite.copy()
        sil.fill((255, 255, 255), special_flags=pygame.BLEND_RGB_SUB)
        solid = pygame.Surface(sprite.get_size())
        solid.fill(color)
        sil.blit(solid, (0, 0), special_flags=pygame.BLEND_RGB_MAX)
        return sil

    def draw_turn_indicators(self):
        """Pawn silhouettes right of the board; the side to move pulses gold."""
        turn = self.game.board.turn
        # glow: smooth 0..1 breathing cycle, ~1.6 s per breath
        wave = 0.5 + 0.5 * math.sin(self.glow_t * math.tau / 1.6 - math.pi / 2)
        gold = COL_GOLD
        dim = (86, 89, 96)
        board_top = self.status_h + self.margin
        board_bottom = self.status_h + self.margin * 2 + self.board_px
        # the side that sits at the bottom of the screen (white by default)
        for side, piece in ((BLACK, "p"), (WHITE, "P")):
            at_bottom = (side == WHITE) != self.flipped
            y = board_bottom - self.pawn_size if at_bottom else board_top
            base = self.sprites[piece]
            scaled = pygame.transform.scale(
                base, (self.pawn_size, self.pawn_size))
            color = tuple(int(dim[c] + (gold[c] - dim[c]) * wave)
                          for c in range(3)) if side == turn else dim
            sil = self._silhouette(scaled, color)
            if side == turn:  # soft halo behind the glowing pawn
                halo = pygame.Surface((self.pawn_size * 2,) * 2, pygame.SRCALPHA)
                pygame.draw.circle(halo, (*color, int(60 * wave)),
                                   (self.pawn_size, self.pawn_size),
                                   int(self.pawn_size * (0.75 + 0.15 * wave)))
                self.screen.blit(halo, (self.pawn_x - self.pawn_size // 2,
                                        y + self.pawn_size // 2 - self.pawn_size))
            self.screen.blit(sil, (self.pawn_x, y))

    # -------------------------------------------------- move history panel

    def _losses(self, entry):
        """What one ply took off the board, as (victim, side credited).

        A capture credits its mover; a mine blast credits the mover's
        opponent, because the mover's own piece walked into it."""
        out = []
        if entry["captured"]:
            out.append((entry["captured"], entry["mover"]))
        if entry.get("blast"):
            out.append((entry["blast"],
                        BLACK if entry["mover"] == WHITE else WHITE))
        return out

    def _history_rows(self):
        """Group plies into (move_number, white_entry, black_entry, advantage)."""
        rows = []
        adv = 0
        for i in range(0, len(self.history), 2):
            white = self.history[i]
            black = self.history[i + 1] if i + 1 < len(self.history) else None
            for entry in (white, black):
                if not entry:
                    continue
                for victim, side in self._losses(entry):
                    value = PIECE_VALUES.get(victim.upper(), 0)
                    adv += value if side == WHITE else -value
            rows.append((i // 2 + 1, white, black, adv))
        return rows

    def _stats_summary(self):
        """Per-side pieces removed, material taken and checks given."""
        stats = {"w": {"cap": 0, "mat": 0, "chk": 0},
                 "b": {"cap": 0, "mat": 0, "chk": 0}}
        for entry in self.history:
            mover = stats.get(entry["mover"])
            if mover is None:
                continue
            for victim, side in self._losses(entry):
                row = stats[side]
                row["cap"] += 1
                row["mat"] += PIECE_VALUES.get(victim.upper(), 0)
            if entry.get("check"):
                mover["chk"] += 1
        return stats

    @staticmethod
    def _win_percent(material_diff):
        """White's win share from the material balance, chess.com style.
        Uses the standard expectation formula on centipawns (1 pawn = 100)."""
        return 1.0 / (1.0 + 10 ** (-material_diff * 100 / 400.0))

    def _fit_text(self, font, text, max_w, color=COL_TEXT):
        """Render text within max_w pixels, eliding with '..'; empty if no room."""
        if max_w <= 0:
            return pygame.Surface((0, 0), pygame.SRCALPHA)
        img = font.render(text, True, color)
        if img.get_width() <= max_w:
            return img
        while text and font.render(text + "..", True, color).get_width() > max_w:
            text = text[:-1]
        img = font.render(text + "..", True, color)
        if img.get_width() > max_w:  # even ".." does not fit
            return pygame.Surface((0, 0), pygame.SRCALPHA)
        return img

    def draw_panel(self):
        rect = self._panel_region()
        self.screen.fill(COL_BG, rect)
        # etched vertical divider from the board area, Win3s-style
        pygame.draw.line(self.screen, xp.SHADOW, (rect.left, rect.top),
                         (rect.left, rect.bottom))
        pygame.draw.line(self.screen, xp.HILITE, (rect.left + 1, rect.top),
                         (rect.left + 1, rect.bottom))
        chat_r, hist_r = self.panel_split(rect)
        if hist_r is not None and self.settings["move_panel"]:
            if self.four:
                self._draw_history4(hist_r)
            else:
                self._draw_history(hist_r)
        if chat_r is not None and self.chat_open:
            self._draw_chat_block(chat_r)

    def _draw_history(self, rect):
        """The move-history section, drawn inside `rect` (which shrinks when
        the docked chat takes the top of the sidebar)."""
        head = self.small_font.render("Move history", True, COL_TEXT)
        self.screen.blit(head, (rect.x + 8, rect.y + 5))
        y = rect.y + 5 + head.get_height() + 5
        y = self.draw_panel_stats(rect, y)

        rows = self._history_rows()
        row_h = self.panel_font.get_height() + 5
        field_r = pygame.Rect(rect.x + 8, y, rect.w - 16,
                              max(48, rect.bottom - 8 - y))
        xp.field(self.screen, field_r)
        track = pygame.Rect(field_r.right - 16, field_r.y + 2, 15,
                            field_r.height - 4)
        view = pygame.Rect(field_r.x + 2, field_r.y + 2,
                           track.x - field_r.x - 4, field_r.height - 4)
        max_scroll = max(0, len(rows) * row_h - view.height)
        if self.follow_latest:
            self.scroll_y = max_scroll
        self.scroll_y = max(0, min(self.scroll_y, max_scroll))
        self.draw_scrollbar(track, len(rows) * row_h, view.height)

        self.screen.set_clip(view)
        # number column wide enough for the largest move number in the list
        num_w = max(26, self.panel_font.size(f"{len(rows) or 1}.")[0] + 8)
        cell_w = max(20, (view.w - num_w - 8) // 2)
        for idx, (num, white, black, adv) in enumerate(rows):
            y = view.y + idx * row_h - self.scroll_y
            if y + row_h < view.y or y > view.bottom:
                continue
            if idx % 2:  # XP list-view style alternating bands
                pygame.draw.rect(self.screen, (243, 241, 230),
                                 (view.x, y, view.w, row_h))
            lbl = self.panel_font.render(f"{num}.", True, COL_MUTED)
            self.screen.blit(lbl, (view.x + 4, y + 2))
            x = view.x + num_w
            adv_txt = f"+{abs(adv)}" if adv else ""
            latest = idx == len(rows) - 1
            self._draw_move_cell(x, y, row_h, white, cell_w,
                                 adv_txt if adv > 0 else "",
                                 latest and black is None)
            self._draw_move_cell(x + cell_w, y, row_h, black, cell_w,
                                 adv_txt if adv < 0 else "", latest and black)
        self.screen.set_clip(None)

    def _draw_history4(self, rect):
        """Cross move list: one ply per row, the army already carried inside
        each SAN ("w:d12"). The two-player win bar and material tally have no
        meaning in a four-army free-for-all, so this view drops them."""
        head = self.small_font.render("Move history", True, COL_TEXT)
        self.screen.blit(head, (rect.x + 8, rect.y + 5))
        y = rect.y + 5 + head.get_height() + 5
        row_h = self.panel_font.get_height() + 5
        field_r = pygame.Rect(rect.x + 8, y, rect.w - 16,
                              max(48, rect.bottom - 8 - y))
        xp.field(self.screen, field_r)
        track = pygame.Rect(field_r.right - 16, field_r.y + 2, 15,
                            field_r.height - 4)
        view = pygame.Rect(field_r.x + 2, field_r.y + 2,
                           track.x - field_r.x - 4, field_r.height - 4)
        n = len(self.history)
        max_scroll = max(0, n * row_h - view.height)
        if self.follow_latest:
            self.scroll_y = max_scroll
        self.scroll_y = max(0, min(self.scroll_y, max_scroll))
        self.draw_scrollbar(track, n * row_h, view.height)
        self.screen.set_clip(view)
        num_w = max(26, self.panel_font.size(f"{n or 1}.")[0] + 8)
        cell_w = max(20, view.w - num_w - 8)
        for idx, entry in enumerate(self.history):
            yy = view.y + idx * row_h - self.scroll_y
            if yy + row_h < view.y or yy > view.bottom:
                continue
            if idx % 2:  # XP list-view style alternating bands
                pygame.draw.rect(self.screen, (243, 241, 230),
                                 (view.x, yy, view.w, row_h))
            lbl = self.panel_font.render(f"{idx + 1}.", True, COL_MUTED)
            self.screen.blit(lbl, (view.x + 4, yy + 2))
            self._draw_move_cell(view.x + num_w, yy, row_h, entry, cell_w,
                                 "", idx == n - 1)
        self.screen.set_clip(None)

    def draw_scrollbar(self, track, content_h, view_h):
        """Static but authentic-looking XP scrollbar (gutter + thumb)."""
        self.screen.fill((247, 246, 238), track)
        sbw = track.width
        up = pygame.Rect(track.x, track.y, sbw, sbw)
        down = pygame.Rect(track.x, track.bottom - sbw, sbw, sbw)
        rail = pygame.Rect(track.x, up.bottom, sbw, max(0, down.y - up.bottom))
        self.screen.fill((236, 233, 216), rail)
        for b, dy in ((up, -1), (down, 1)):
            xp.gradient_v(self.screen, b, xp.BTN_TOP, xp.BTN_BOT)
            pygame.draw.rect(self.screen, xp.BTN_BORDER, b, 1)
            cx, cy = b.centerx, b.centery
            pygame.draw.polygon(self.screen, (70, 70, 70),
                                [(cx - 3, cy + dy * 2), (cx + 3, cy + dy * 2),
                                 (cx, cy - dy * 3)])
        if content_h > view_h and rail.height > 18:
            ratio = min(1.0, view_h / max(1, content_h))
            thumb_h = max(18, int(rail.height * ratio))
            span = max(1, content_h - view_h)
            t = self.scroll_y / span
            thumb = pygame.Rect(rail.x,
                                rail.y + int((rail.height - thumb_h) * t),
                                sbw, thumb_h)
            xp.gradient_v(self.screen, thumb, xp.BTN_TOP, xp.BTN_BOT)
            pygame.draw.rect(self.screen, xp.BTN_BORDER, thumb, 1)
            pygame.draw.line(self.screen, xp.HILITE,
                             (thumb.x + 4, thumb.centery - 2),
                             (thumb.right - 5, thumb.centery - 2))
            pygame.draw.line(self.screen, xp.SHADOW,
                             (thumb.x + 4, thumb.centery + 1),
                             (thumb.right - 5, thumb.centery + 1))

    def draw_panel_stats(self, rect, y):
        """Win-chance bar plus material / captures / checks, chess.com style,
        drawn as an XP progress bar."""
        stats = self._stats_summary()
        diff = stats["w"]["mat"] - stats["b"]["mat"]
        pct_w = self._win_percent(diff)

        bar = pygame.Rect(rect.x + 8, y, rect.w - 16,
                          max(14, self.small_font.get_height() + 3))
        # black's share is the selection-blue ground, white's the light chunk
        pygame.draw.rect(self.screen, xp.SELECTION, bar)
        white_w = int(bar.w * pct_w)
        if white_w > 0:
            chunk = pygame.Rect(bar.x, bar.y, white_w, bar.h)
            xp.gradient_v(self.screen, chunk, (255, 255, 255), (224, 224, 214))
            if white_w > 4:
                pygame.draw.line(self.screen, xp.HILITE,
                                 (chunk.x + 1, chunk.y + 1),
                                 (chunk.right - 2, chunk.y + 1))
        pygame.draw.rect(self.screen, xp.DK_SHADOW, bar, 1)
        lbl_y = bar.y + (bar.h - self.small_font.get_height()) // 2
        left = self.small_font.render(f"{pct_w * 100:.0f}%", True, (25, 25, 25))
        right = self.small_font.render(f"{(1 - pct_w) * 100:.0f}%",
                                       True, (255, 255, 255))
        if white_w > left.get_width() + 6:
            self.screen.blit(left, (bar.x + 4, lbl_y))
        if bar.w - white_w > right.get_width() + 6:
            self.screen.blit(right, (bar.right - right.get_width() - 4, lbl_y))

        y = bar.bottom + 4
        mat_text = (f"Material {'+' if diff > 0 else ''}{diff}" if diff
                    else "Material even")
        mat = self._fit_text(self.small_font, mat_text, bar.w,
                             COL_GOLD if diff else COL_MUTED)
        self.screen.blit(mat, (bar.x, y))
        y += mat.get_height() + 2
        tally_text = (f"Captures {stats['w']['cap']}-{stats['b']['cap']}   "
                      f"Checks {stats['w']['chk']}-{stats['b']['chk']}")
        tally = self._fit_text(self.small_font, tally_text, bar.w, COL_MUTED)
        self.screen.blit(tally, (bar.x, y))
        return y + tally.get_height() + 8

    def _capture_icon(self, gone):
        """The mini sprite drawn beside a SAN move for the piece it removed.
        Two-player codes ("P", "q") come off `minis`; a cross game's codes
        ("Pw", "Kbl") come off `minis4`. Anything unknown shows no icon rather
        than crashing the history panel."""
        if not gone:
            return None
        icon = self.minis.get(gone)
        if icon is None:
            icon = getattr(self, "minis4", {}).get(gone)
        return icon

    def _draw_move_cell(self, x, y, row_h, entry, cell_w, adv_text="",
                        latest=False):
        """One SAN cell: text (truncated to fit), capture icon, +advantage."""
        if not entry:
            return
        pad = 3
        adv_img = (self.small_font.render(adv_text, True, COL_GOLD)
                   if adv_text else None)
        adv_w = adv_img.get_width() + 2 if adv_img else 0
        gone = entry["captured"] or entry.get("blast")
        mini = self._capture_icon(gone)
        mini_w = mini.get_width() + 2 if mini else 0
        color = xp.CAP_MID if latest else COL_TEXT
        img = self._fit_text(self.panel_font, entry["san"],
                             cell_w - adv_w - mini_w - pad * 2, color)
        self.screen.blit(img, (x + pad, y + (row_h - img.get_height()) // 2))
        if mini and x + pad + img.get_width() + mini_w <= x + cell_w - adv_w:
            self.screen.blit(mini, (x + pad + img.get_width() + 2,
                                    y + (row_h - mini.get_height()) // 2))
        if adv_img:
            self.screen.blit(adv_img, (x + cell_w - adv_img.get_width() - pad,
                                       y + (row_h - adv_img.get_height()) // 2))

    # ------------------------------------------------------- picker & overlay

    def draw_promotion_picker(self):
        w, h = self.window
        sq = self.square
        color = self.game.board.turn
        choices = "QRBN" if color == WHITE else "qrbn"  # uppercase = white sprites
        cap_h = 24
        pw, ph = sq * len(choices) + 12, sq + cap_h + 16
        rect = pygame.Rect((w - pw) // 2, (h - ph) // 2, pw, ph)
        xp.message_frame(self.screen, rect, "Promote to", self.small_font,
                         icon=getattr(self, "app_icon", None))
        mouse = pygame.mouse.get_pos()
        self.promo_choices = []
        for i, ch in enumerate(choices):
            cell = pygame.Rect(rect.x + 6 + i * sq, rect.y + cap_h + 8,
                               sq, sq)
            xp.button(self.screen, cell, "", self.font,
                      hovered=cell.collidepoint(mouse))
            sprite = self.sprites[ch]
            offset = (sq - sprite.get_width()) // 2
            self.screen.blit(sprite, (cell.x + offset, cell.y + offset))
            self.promo_choices.append((cell, ch))

    def draw_game_over(self):
        """Result shown as a classic XP message dialog, no dimming; the
        caption's X dismisses it and the board stays visible behind."""
        panel = self.over_panel_rect()
        body, close = xp.message_frame(self.screen, panel, "FligChess",
                                       self.font,
                                       icon=getattr(self, "app_icon", None))
        self._over_close = close
        self._over_cap = pygame.Rect(panel.x + 2, panel.y + 2,
                                     panel.w - 4, 24)
        img = self._over_text_img()
        self.screen.blit(img, (panel.centerx - img.get_width() // 2,
                               body.y + 14))
        mouse = pygame.mouse.get_pos()
        for i, (rect, _action) in enumerate(self.over_rects()):
            label = ("Rematch", "Menu")[i]
            xp.button(self.screen, rect, label, self.font,
                      default=i == 0, hovered=rect.collidepoint(mouse))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="fligchess", description="FligChess - pygame chess with bots")
    ap.add_argument("--white", metavar="BOT", help="bot playing White")
    ap.add_argument("--black", metavar="BOT",
                    help="bot playing Black; naming only one of the two"
                         " leaves the other side to a human")
    ap.add_argument("--players", type=int, choices=(2, 4), default=2,
                    help="army count for --autoplay: 4 opens a cross game")
    ap.add_argument("--red", metavar="BOT",
                    help="bot playing Red (a cross game; --white names the"
                         " rest unless they too are named)")
    ap.add_argument("--blue", metavar="BOT",
                    help="bot playing Blue (a cross game)")
    ap.add_argument("--variant", metavar="KEY", choices=list(PICKS["variant"]),
                    help="rules variant: classic, fog or minefield")
    ap.add_argument("--monitor", metavar="NAME",
                    help="open on the monitor whose name contains NAME (e.g. dell)")
    ap.add_argument("--fullscreen", action="store_true",
                    help="start fullscreen without touching saved settings")
    ap.add_argument("--budget", type=float, metavar="SEC",
                    help="override every bot's thinking seconds per move")
    ap.add_argument("--fast", action="store_true",
                    help="minimal pause between moves")
    ap.add_argument("--autoplay", action="store_true",
                    help="start a game immediately on launch")
    ap.add_argument("--report", metavar="FILE",
                    help="keep writing the game PGN to FILE; quit when it ends")
    ChessApp(ap.parse_args(argv)).run()


if __name__ == "__main__":
    main()
