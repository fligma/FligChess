"""Public API for FligChess bots.

Any .py file dropped into the `bots/` folder next to the app is imported at
startup. If it defines a subclass of `Bot`, that bot can be seated on either
colour from the player setup page, and its `CONFIG` entries appear there as
dropdowns of their own.

Bots should only import from `fligchess.core` (this module re-exports
`Board`, `Move`, `WHITE` and `BLACK` through it). See `bots/fligbot.py`
for a complete example.
"""

import importlib.util
import sys
from pathlib import Path

from fligchess.core.board import Board, Move, WHITE, BLACK  # noqa: F401


class Position:
    """The read-only board snapshot handed to `Bot.choose`.

    Treat it as immutable: `make()` returns a *new* Position with the move
    applied, which doubles as undo for search code.
    """

    __slots__ = ("_board",)

    def __init__(self, board=None, fen=None):
        if fen is not None:
            board = Board.from_fen(fen)
        elif isinstance(board, str):
            board = Board.from_fen(board)
        self._board = board if board is not None else Board.startpos()

    @property
    def turn(self):
        """Side to move: WHITE or BLACK."""
        return self._board.turn

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
        return Position(after)

    def in_check(self, color=None):
        return self._board.in_check(color)

    def find_king(self, color):
        return self._board.find_king(color)

    def is_attacked(self, sq, by):
        return self._board.is_attacked(sq, by)

    def is_game_over(self):
        """True on checkmate or stalemate (no legal moves for the side to move)."""
        return not self._board.legal_moves()

    def __repr__(self):
        return f"Position({self.fen()})"


class Bot:
    """Base class every bot in `bots/` subclasses.

    Class attributes:
        name    -- shown in the bot list, must be unique per file.
        version -- integer release number; the bot updater compares it with
                   the remote index.json to decide whether to download.
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
    CONFIG = {}

    def __init__(self):
        self.options = {key: spec["default"] for key, spec in self.CONFIG.items()}

    def configure(self, options):
        """Apply saved option values, ignoring anything not a valid choice."""
        for key, spec in self.CONFIG.items():
            value = options.get(key, spec["default"])
            if value in spec["choices"]:
                self.options[key] = value

    def choose(self, position):
        """Return a Move from position.legal_moves(), or None to pass.

        Runs in a background thread: touch no game/app state in here, and
        keep it pure -- only the returned move is used.
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
