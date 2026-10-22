"""fligbot fog - the imperfect-info engine of FligChess, one model for
both the two-player board and the four-army cross.

Fog of war hands a bot a *blurred* board: the app blanks every enemy the
viewer may not see, so the engine must reason only over the pieces it is
actually shown and must never lean on material hiding in the fog. `fligbot
classic 4p` already plays both board shapes off a single flat engine, and
this bot rides that proven search - the same alpha-beta, transposition
table and multi-army evaluation - but tunes it for the veil and, crucially,
only ever plays a move the blurred position genuinely allows: it takes the
search's ranking strongest-first and steps down it until a move maps onto a
legal one, so an idea that survived only because a hidden blocker was not
there gets dropped rather than played.

It advertises the fog variant alone ("2p/*/fog", "4p/*/fog"), so the app
seats it on fog games and never reaches for it in a classic or minefield
match - unless the browser's force-all-modes override says otherwise. Run it
to self-test: `python bots/fligbot_fog.py`.
"""

import pathlib
import random
import sys

try:
    import fligchess.core                                  # noqa: F401
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                           / "src"))

from fligchess.core import Bot, Position, GameInfo          # noqa: E402

# The search geometry and the engine itself are the proven code of
# `fligbot classic 4p`; the fog bot only restricts where it is seated and
# re-shapes the answer so it is always legal on the *visible* board, so the
# two engines can never drift apart on move legality.
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import fligbot_classic_4p as _classic                       # noqa: E402

ARMY_OF = _classic.ARMY_OF
CROSS = _classic.CROSS
MATE = _classic.MATE
MATE_THRESHOLD = _classic.MATE_THRESHOLD
PROMO_CHAR = _classic.PROMO_CHAR
_xy = _classic._xy


class FligBotFog(Bot):
    name = "fligbot fog"
    author = "fligchess"
    version = 1                 # v1: one fog-aware engine for both boards
    CONFIG = {}                 # no per-seat options: it always plays to win
    MODES = ("2p/*/fog", "4p/*/fog")   # the veil only, whatever the net
    says = True                 # it announces a finished game through chat

    budget = 0.6                # a little thought per move; --budget overrides
    MAX_SEARCH_DEPTH = 20       # iterative deepening is cut off by time

    def __init__(self):
        super().__init__()
        self._engine = _classic.Engine()

    # ------------------------------------------------------------- search

    def choose(self, position):
        """Its strongest move the visible board allows. The search runs on
        exactly what the bot can see (the app has already blurred the rest),
        then the ranking is walked strongest-first for the first move that is
        genuinely legal here, so a fog-hidden blocker cannot leave the engine
        stranded on an idea that only worked on the clean board. In a game it
        has already won it says so in the chat."""
        moves = position.legal_moves()
        if not moves:
            return None
        eng = self._engine
        eng.load(position.board)
        cross = eng.shape == CROSS          # the four-army board, not mapped 2P
        army = ARMY_OF[position.turn] if cross else (
            0 if position.turn == "w" else 1)
        m, score, scores = eng.search(army, self.budget, self.MAX_SEARCH_DEPTH)
        mv = self._best_visible(position, moves, m, scores, score, cross)
        if mv is None:
            return random.choice(moves)
        info = position.info
        if info is not None and abs(score) >= MATE_THRESHOLD:
            plies = MATE - abs(score)
            if info.players == 2:
                info.say("mate in %d" % ((plies + 1) // 2))
            else:
                info.say("last king standing in %d moves"
                         % max(1, (plies + 2) // 3))
        return mv

    def _best_visible(self, position, moves, best, scores, score, cross):
        """The search's top-ranked move that this (visible) board can really
        play: walk the scored moves strongest-first and return the first that
        maps to a legal move, falling back to the raw pick."""
        ranked = sorted(scores, key=lambda m: -scores[m]) if scores else []
        if best is not None and best not in ranked:
            ranked.insert(0, best)
        for cand in ranked:
            mv = self._external(position, moves, cand, cross)
            if mv is not None:
                return mv
        if best is not None:
            return self._external(position, moves, best, cross)
        return None

    @staticmethod
    def _external(position, moves, m, cross):
        """The internal packed move as the board's own Move object, or None
        when this (already blurred) position does not actually allow it."""
        if m is None:
            return None
        frm, to = m & 255, (m >> 8) & 255
        promo = PROMO_CHAR.get((m >> 16) & 7)
        if cross:
            src, dst = _xy(frm), _xy(to)
        else:
            sx, sy = _xy(frm)
            dx, dy = _xy(to)
            src, dst = (sx - 3, 9 - sy), (dx - 3, 9 - dy)
        want = (src, dst, promo)
        for mv in moves:
            if (mv.from_sq, mv.to_sq, mv.promotion) == want:
                return mv
        return None


if __name__ == "__main__":
    # tiny self-test: python bots/fligbot_fog.py
    from fligchess.core import variants
    from fligchess.core.board import Board
    from fligchess.core.board4 import Board4
    bot = FligBotFog()
    bot.configure({})
    bot.budget = 0.3
    # the same blurred board the app would hand a fog player: a legal move
    # on the classic board under the veil
    fog2 = variants.create("fog", four=False)
    real2 = Board.from_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR "
                           "w KQkq - 0 1")
    p2 = Position(board=fog2.bot_view(real2),
                  info=GameInfo(players=2, variant="fog", net="offline",
                                army="w"))
    mv = bot.choose(p2)
    print(f"{bot.name} 2p fog picks:", mv,
          "legal" if mv in p2.legal_moves() else "ILLEGAL")
    # a legal move across the four-army cross under the veil
    fog4 = variants.create("fog", four=True)
    p4 = Position(board=fog4.bot_view(Board4.startpos()),
                  info=GameInfo(players=4, variant="fog", net="offline",
                                army="w"))
    mv = bot.choose(p4)
    print(f"{bot.name} 4p fog picks:", mv,
          "legal" if mv in p4.legal_moves() else "ILLEGAL")
    # it finds a free mate and announces it
    im = GameInfo(players=2, variant="fog", net="duel", army="w")
    pm = Position(fen="6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", info=im)
    mv = bot.choose(pm)
    print(f"{bot.name} mate-in-1:", mv, "says", im.drain())
