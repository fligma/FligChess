"""Board variants: classic, fog of war and minefield.

A variant never touches the move generator. It only answers two questions -
what a player is allowed to *see*, and what happens the instant a piece
lands - so the standard rules, perft and the bots stay untouched.

All of its state is derived: the fog layer comes from the position, the
minefield from one seed plus the move list. That means the app's
rebuild-by-replay undo needs no snapshots at all - `replay()` recomputes
everything and stays exact, and `create(key, seed)` hands the rebuild a
variant that lays the very same mines the game started with.
"""

import random

from fligchess.core.board import Board, WHITE, BLACK, color_of
from fligchess.core.board4 import (Board4, playable as _playable4,
                                   army_of as _army_of, kind_of as _kind_of)

CLASSIC, FOG, MINEFIELD = "classic", "fog", "minefield"

# the order the game-setup screens list the variants in
VARIANTS = (CLASSIC, FOG, MINEFIELD)

MINE_COUNT = 8                  # mines laid down for a new minefield game
MINE_RANKS = (2, 3, 4, 5)       # the four ranks that are empty at the start


def _is_cross(board):
    """A Board4 (the 14x14 cross) exposes ``grid``; the classic board does not."""
    return hasattr(board, "grid")


class Variant:
    """Classic chess: nothing hidden, nothing explodes."""

    key = CLASSIC
    label = "Classic"
    blurb = "Standard rules, the whole board in plain sight."

    def __init__(self, seed=None, four=False):
        """Variants that lay anything on the board lay it from `seed`; the
        rest have nothing to derive, so they simply keep hold of it.
        `four` marks a cross-board (four-army) game, whose minefield and fog
        are computed over the 14x14 grid instead of the classic 8x8."""
        self.seed = seed
        self.four = four

    def replay(self, moves):
        """Re-derive everything from the moves played so far."""

    def hidden(self, board, viewer=None):
        """Squares `viewer` (default: the side to move) may not see."""
        return frozenset()

    def bot_view(self, board):
        """The position a bot is allowed to think about."""
        return board.clone()

    def after_move(self, board, move):
        """Runs as soon as the move is on the board.

        Returns the [(square, piece)] pairs this move just killed, so the
        caller can animate them; may edit the board (a blast removes the
        piece that trod the mine).
        """
        return []

    def markers(self, board):
        """Hidden things worth showing once the game is over."""
        return frozenset()


class FogOfWar(Variant):
    """You see your own pieces and the squares they attack or defend.

    Everything else stays under fog: pieces standing there are not drawn,
    and the bot is handed the same blurred view instead of the real board.
    A blurred view can tempt a bot into a move that is illegal once the
    hidden pieces count, and the app then plays the closest legal move.
    Both kings stay visible: a hunt needs a quarry, and an engine with no
    king to aim at stops making sense.
    """

    key = FOG
    label = "Fog of War"
    blurb = "Squares your army cannot reach stay hidden."

    def hidden(self, board, viewer=None):
        if _is_cross(board):
            return self._hidden_cross(board, viewer)
        me = viewer or board.turn
        visible = set()
        for rank in range(8):
            for file_ in range(8):
                sq = (file_, rank)
                piece = board.get(sq)
                mine = piece is not None and color_of(piece) == me
                if mine or board.is_attacked(sq, me):
                    visible.add(sq)
        hidden = {(f, r) for r in range(8)
                  for f in range(8)} - visible
        for color in (WHITE, BLACK):        # the kings are never under fog
            king = board.find_king(color)
            if king is not None:
                hidden.discard(king)
        return frozenset(hidden)

    def _hidden_cross(self, board, viewer):
        """The same rule on the cross: you see your own army and the squares
        it reaches. Every standing king stays visible - a hunt needs a quarry
        - and the four off-board corners are never in the set."""
        me = viewer or board.turn
        cells = [(x, y) for y in range(14) for x in range(14)
                 if _playable4((x, y))]
        visible = set()
        for sq in cells:
            piece = board.get(sq)
            if (piece is not None and _army_of(piece) == me
                    or board.is_attacked(sq, me)):
                visible.add(sq)
        hidden = set(cells) - visible
        for army in ("w", "b", "r", "bl"):
            king = board.find_king(army)
            if king is not None:
                hidden.discard(king)
        return frozenset(hidden)

    def bot_view(self, board):
        view = board.clone()
        cross = _is_cross(board)
        turn = board.turn
        for sq in self.hidden(board):
            piece = view.get(sq)
            if piece is None:
                continue
            owner = _army_of(piece) if cross else color_of(piece)
            if owner != turn:
                view.set(sq, None)          # unseen enemies are not there
        return view


def _is_king(board, piece):
    """Whether a piece is a king, on either board shape."""
    return _kind_of(piece) == "K" if _is_cross(board) else piece.upper() == "K"


class Minefield(Variant):
    """Mines hidden in the empty middle of the board.

    Anything but a king that lands on one dies with it, and the mine is
    spent. Nobody can see the mines, so the bot blunders into them on the
    same terms as a human; whatever is still armed is shown when the game
    ends.
    """

    key = MINEFIELD
    label = "Minefield"
    blurb = "Hidden mines destroy whatever steps on them - kings excepted."

    def __init__(self, seed=None, four=False):
        self.seed = random.randrange(1 << 40) if seed is None else seed
        self.four = four
        self.mines = frozenset()
        self.blasted = frozenset()
        self.replay([])         # the field is laid the moment the variant is

    def _mine_field(self):
        """The squares a mine may sit on: the empty middle of whichever board.
        On the cross that is every playable cell still clear at the start."""
        if self.four:
            board = Board4.startpos()
            return (board, [(x, y) for y in range(14) for x in range(14)
                            if _playable4((x, y)) and board.get((x, y)) is None])
        return (Board.startpos(),
                [(f, r) for r in MINE_RANKS for f in range(8)])

    def replay(self, moves):
        """Lay the mines from the seed, then detonate the ones the moves
        already trod, so an undo lands on exactly the same board."""
        board, squares = self._mine_field()
        self.mines = frozenset(random.Random(self.seed).sample(squares,
                                                               MINE_COUNT))
        self.blasted = frozenset()      # every state here comes from the seed
        for move in moves:
            board.make_move(move)
            self.after_move(board, move)

    def after_move(self, board, move):
        sq = move.to_sq
        piece = board.get(sq)
        if sq not in self.mines or piece is None or _is_king(board, piece):
            return []
        self.mines = self.mines - {sq}
        self.blasted = self.blasted | {sq}
        board.set(sq, None)
        board.halfmove = 0          # like a capture, it resets the clock
        return [(sq, piece)]

    def markers(self, board):
        return self.mines


REGISTRY = {cls.key: cls for cls in (Variant, FogOfWar, Minefield)}


def create(key, seed=None, four=False):
    """A fresh variant for a new game; an unknown key plays classic.
    Passing a `seed` rebuilds the very same field a previous game of that
    variant laid, which is what an undo replays its moves against. `four`
    marks the four-army cross board."""
    return REGISTRY.get(key, Variant)(seed, four)


def info(key):
    """(label, blurb) of a variant, for the setup screens."""
    cls = REGISTRY.get(key, Variant)
    return cls.label, cls.blurb
