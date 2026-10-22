"""A tiny built-in mover for the four-player cross board.

A 4-player game - an offline AI seat, or a host that chose to play as an
engine - needs a mover even when no four-army bot is installed or seated, so
this is that mover: a greedy picker, no search, an AI seat is always filled
and the built-in mover is a dependency-free safety net rather than a rival
engine. A real four-army bot (``fligbot classic 4p``) can be opted in per
seat; when a seat names none - or that bot fails - the game falls back here.

It reads only ``board4`` and returns a plain ``Move``; nothing here touches
the app or the ``Bot`` interface, so seating a stronger engine never
disturbs it.
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
