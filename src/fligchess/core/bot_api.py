"""Public API for FligChess bots.

Any .py file dropped into the `bots/` folder next to the app is imported at
startup. If it defines a subclass of `Bot`, that bot can be seated on either
colour from the player setup page, and its `CONFIG` entries appear there as
dropdowns of their own.

Bots should only import from `fligchess.core` (this module re-exports
`Board`, `Move`, `WHITE` and `BLACK` through it). See `bots/fligbot_classic_2p.py`
for a complete example.

Every game hands its bot a `GameInfo` on `position.info`: how many armies
play, which variant, whether the game is offline / LAN / an AI duel, and
which seat this bot owns. `info.say("...")` posts a short chat line, and a
bot advertises the games it accepts through the `MODES` class attribute.
"""

import importlib.util
import sys
import threading
from pathlib import Path

from fligchess.core.board import Board, Move, WHITE, BLACK  # noqa: F401


def mode_matches(modes, mode):
    """Whether one of `modes` accepts the mode token `mode`.

    Tokens read "<players>p/<net>/<variant>" (e.g. "2p/lan/classic"). An
    advertised mode matches two ways: by prefix, so ("2p",) takes every
    two-player game and ("4p/lan",) only LAN cross games; or segment-wise, so
    a token of the same length whose segments each equal the game's or are
    the "*" wildcard matches - ("2p/*/classic") takes every two-player
    classic game whatever the net, and ("4p/*/fog") only four-army fog."""
    parts = mode.split("/")
    for m in modes:
        if mode == m or mode.startswith(m + "/"):
            return True
        declared = m.split("/")
        if len(declared) == len(parts) and all(
                d == "*" or d == p for d, p in zip(declared, parts)):
            return True
    return False


def net_flag(target, net):
    """The LAN/DUEL shorthand a bot may set to overrule the net half of its
    mode matching: None defers to MODES, True and False decide for that net.
    Anything else ("offline") has no flag."""
    if net == "lan":
        return getattr(target, "LAN", None)
    if net == "duel":
        return getattr(target, "DUEL", None)
    return None


def bot_allows(target, mode):
    """Whether the bot class or instance `target` plays the game `mode`.

    Its `MODES` tokens first, then the LAN/DUEL flags have the last word on
    their own net: True seats the bot where MODES alone would not reach, and
    only for an army count it advertises; False refuses that net even where
    a prefix would have let it in."""
    modes = tuple(getattr(target, "MODES", ()) or ())
    ok = mode_matches(modes, mode)
    parts = mode.split("/")
    flag = net_flag(target, parts[1] if len(parts) > 1 else "")
    if flag is None:
        return ok
    if ok:
        return bool(flag)
    return bool(flag) and any(parts[0] == m.split("/")[0] for m in modes)


class GameInfo:
    """Read-only description of the game a bot is playing.

    Attributes (all plain values, safe to read from a thinking thread):
        players -- 2 or 4
        variant -- the rules key ("classic", "fog", "minefield")
        net     -- "offline", "lan" (hosted or joining) or "duel"
                   (AI vs AI)
        army    -- this bot's own side: WHITE/BLACK in a two-player
                   game, "w"/"b"/"r"/"bl" across the four-army cross
        mode    -- one token bundling all of the above, for bots that
                   only want to branch on the whole arrangement
        seen    -- how many times the position now offered to this mover has
                   already occurred in the game (a repetition-loss warning;
                   0 when the app does not track it, e.g. under fog)
        recent  -- a frozenset of the position signatures played recently, so
                   a bot can tell a forward move from a shuffle without
                   knowing the app's exact counting rule; empty when unknown

    `say(text)` queues a short chat line for the game (used in duels and
    lobbies to announce stats such as "mate in 3"). It is thread-safe and
    never raises; the app drains the queue at its own pace.
    """

    __slots__ = ("players", "variant", "net", "army", "mode",
                 "seen", "recent", "_lock", "_says")

    def __init__(self, players=2, variant="classic", net="offline",
                 army=None, seen=0, recent=frozenset()):
        self.players = players
        self.variant = variant
        self.net = net
        self.army = army
        self.mode = "%dp/%s/%s" % (players, net, variant)
        self.seen = seen
        self.recent = recent
        self._lock = threading.Lock()
        self._says = []

    def say(self, text):
        """Post one chat line from the bot (ignored when blank)."""
        text = str(text or "").strip()[:200]
        if not text:
            return
        with self._lock:
            self._says.append(text)

    def drain(self):
        """Take every queued chat line; for the app, not for bots."""
        with self._lock:
            out, self._says = self._says, []
        return out

    def __repr__(self):
        return f"GameInfo({self.mode}, army={self.army!r})"


class Position:
    """The read-only board snapshot handed to `Bot.choose`.

    Treat it as immutable: `make()` returns a *new* Position with the move
    applied, which doubles as undo for search code.

    The same class carries both board shapes: an 8x8 `Board` in a
    two-player game and the four-army cross board in a four-player one,
    where `turn` is the army id and `info` says what is going on.
    """

    __slots__ = ("_board", "_info")

    def __init__(self, board=None, fen=None, info=None):
        if fen is not None:
            board = Board.from_fen(fen)
        elif isinstance(board, str):
            board = Board.from_fen(board)
        self._board = board if board is not None else Board.startpos()
        self._info = info

    @property
    def info(self):
        """The `GameInfo` for this game, or None on a bare hand-built
        position (tests and offline analysis always see a plain board)."""
        return self._info

    @property
    def turn(self):
        """Side to move: WHITE or BLACK, or the army id in a 4P game."""
        return self._board.turn

    @property
    def board(self):
        """The board object behind this snapshot, for engines that want to
        read its grid directly (a cross `Board4` carries `grid`, the classic
        one `squares`) instead of going through FEN text.

        Read-only by convention: `make()` is how a Position moves forward,
        and a search that edits this board edits the game's own."""
        return self._board

    def fen(self):
        return self._board.to_fen()

    def piece_at(self, sq):
        """Piece char on (file, rank), or None. rank 0 = white's home."""
        return self._board.get(sq)

    def legal_moves(self):
        return self._board.legal_moves()

    def moves_from(self, sq):
        return [m for m in self._board.legal_moves() if m.from_sq == sq]

    def make(self, move):
        """Return a new Position with `move` applied. This one is unchanged."""
        after = self._board.clone()
        after.make_move(move)
        return Position(after, info=self._info)

    def in_check(self, color=None):
        return self._board.in_check(color)

    def find_king(self, color):
        return self._board.find_king(color)

    def is_attacked(self, sq, by):
        return self._board.is_attacked(sq, by)

    def is_game_over(self):
        """True when the side to move has no legal move (mate or stalemate;
        in a 4P game, when *this army* is mated and the layer eliminates it)."""
        return not self._board.legal_moves()

    def __repr__(self):
        return f"Position({self.fen()})"


class Bot:
    """Base class every bot in `bots/` subclasses.

    Class attributes:
        name    -- shown in the bot list, must be unique per file.
        version -- integer release number; the bot updater compares it with
                   the remote index.json to decide whether to download.
        MODES   -- the game shapes this bot accepts, as mode tokens
                   ("2p", "4p", "4p/lan", "2p/*/fog", ...). A token matches
                   the game's "<players>p/<net>/<variant>" by prefix, or
                   segment-wise with "*" standing for any one segment, so
                   "2p/*/classic" takes every two-player classic game and
                   "4p/*/fog" only four-army fog. Declare only the shapes the
                   search really plays - a two-player engine lists just 2p, a
                   cross engine just 4p - so a setup page offers nothing it
                   would have to refuse at the seat. The app only seats the bot
                   where its modes fit, unless the "force allow all modes"
                   toggle in the bot browser is on.
        LAN / DUEL -- shorthand overrides for the net part of a token, for a
                   bot that would rather state those two separately: True
                   seats it in LAN (or AI-duel) games its MODES do not cover
                   on their own, False refuses that net even when MODES
                   would. Left unset (None) MODES decides alone.
        says    -- True when the bot talks through `GameInfo.say`; the app
                   drains those lines into the game's chat either way, so
                   this only labels the bot for the docs and the tests.
        CONFIG  -- option key -> {"label", "choices", "default"}; each entry
                   gets a dropdown in that player's column and is persisted
                   per seat in config/settings.json, so the same bot can be
                   seated twice at different settings.

    Instance state after init: `self.options` holds the current value of each
    CONFIG key; the app calls `configure()` with the saved values before a game.
    """

    name = "Unnamed bot"
    author = "anonymous"
    version = 1
    MODES = ("2p",)
    LAN = None
    DUEL = None
    says = False
    CONFIG = {}

    def __init__(self):
        self.options = {key: spec["default"] for key, spec in self.CONFIG.items()}

    def configure(self, options):
        """Apply saved option values, ignoring anything not a valid choice."""
        for key, spec in self.CONFIG.items():
            value = options.get(key, spec["default"])
            if value in spec["choices"]:
                self.options[key] = value

    def allows(self, mode):
        """Whether this bot plays the game described by `mode` (a token
        like "2p/offline/classic"); see `bot_allows` and `mode_matches`."""
        return bot_allows(self, mode)

    def choose(self, position):
        """Return a Move from position.legal_moves(), or None to pass.

        Runs in a background thread: touch no game/app state in here,
        and keep it pure -- only the returned move is used. The one
        side effect a bot may make is `position.info.say(...)`, which
        posts a chat line.
        """
        raise NotImplementedError


def load_bots(directory):
    """Import every .py in `directory`; return {bot.name: Bot subclass}.

    A broken bot file is skipped rather than crashing the app.
    """
    bots = {}
    directory = Path(directory)
    if not directory.is_dir():
        return bots
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        mod_name = f"fligchess_bot_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            module = importlib.util.module_from_spec(spec)
            # register before exec so the class is introspectable later
            # (inspect.getsourcefile resolves through sys.modules); the
            # namespaced name keeps it clear of any real import
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - a bad bot must not kill the app
            print(f"bot '{path.name}' failed to load: {exc}")
            sys.modules.pop(mod_name, None)
            continue
        for obj in vars(module).values():
            if (isinstance(obj, type) and issubclass(obj, Bot)
                    and obj is not Bot and getattr(obj, "name", None)):
                bots[obj.name] = obj
    return bots
