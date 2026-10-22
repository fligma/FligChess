"""A tiny built-in mover for the four-player cross board.

The shipped bot (``fligbot``) is a two-player engine and is deliberately not
retrofitted for four armies this release, so a 4-player game - an offline AI
seat, or a host that chose to play as an engine - needs its own driver. This
is that driver: a greedy picker, no search, so an AI seat is always filled
and the built-in mover is a dependency-free safety net rather than a rival
engine.

It reads only ``board4`` and returns a plain ``Move``; nothing here touches
the app or the ``Bot`` interface, so a real four-army bot could replace it
later without disturbing anything.
"""

import random

from fligchess.core.board4 import kind_of

# Rough chess values, king set very high so a capture that takes one is the
# obvious top choice and a move handing a king over (which legality already
# prevents for the mover's own king) stays unattractive.
_VALUES = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9, "K": 1000}


def choose(board, army=None, rng=None):
    """Pick a legal move for `army` (default the side to move).

    Greedy: the move capturing the most valuable enemy piece wins, promotion
    nudging a pawn up; ties break at random so the mover is not deterministic.
    Returns None when the army has no legal move (it is mated or stalemated -
    the game layer then eliminates it)."""
    moves = board.legal_moves(army)
    if not moves:
        return None
    rng = rng or random
    best, score = None, None
    for move in moves:
        s = _score(board, move)
        if score is None or s > score:
            best, score = move, s
        elif s == score and rng.random() < 0.5:
            best = move
    return best


def _score(board, move):
    victim = board.get(move.to_sq)
    value = _VALUES.get(kind_of(victim), 0) if victim is not None else 0
    if move.promotion:
        value += _VALUES.get(move.promotion.upper(), 0)
    return value
