"""fligbot blitz - the fast, shallow engine of FligChess.

Where `fligbot classic` and `fligbot classic 4p` search for strength, blitz
searches for tempo: a tiny thinking budget and a low depth cap, but every
node as cheap as possible. It reuses the cross/centre geometry, the flat
make/undo and the transposition table of the classic four-army engine, then
turns the dial the other way - aggressive move ordering (best-from-TT, then
captures, then killers) so alpha-beta bites on the first few tries, a hard
depth ceiling that iterative deepening almost never reaches, and the blunder
guard left on so a rushed move still refuses to hand a piece straight over.

The result is meant for the two-player board and each seat is meant to move
in a fraction of a second; the shared cross engine lets it still take a
four-player seat when the browser's force-all-modes override is on. Run it
to self-test: `python bots/fligbot_blitz.py`.
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

# The search geometry (shapes, move packing, the flat engine itself) is the
# proven code of `fligbot classic 4p`; blitz only re-tunes how deep and how
# long it looks, so the two engines can never drift apart on legality.
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import fligbot_classic_4p as _classic                       # noqa: E402

# Blitz borrows the classic engine's 2P opening book so it does not have to
# invent an opening at a fraction of a second. Imported defensively: if only
# blitz is installed the import fails and it simply searches from move one.
try:
    import fligbot_classic as _c2p
except Exception:
    _c2p = None

ARMY_OF = _classic.ARMY_OF
CROSS = _classic.CROSS
MATE = _classic.MATE
MATE_THRESHOLD = _classic.MATE_THRESHOLD
PROMO_CHAR = _classic.PROMO_CHAR
_xy = _classic._xy


class FligBotBlitz(Bot):
    name = "fligbot blitz"
    author = "fligchess"
    version = 3                 # v3: modes narrowed to classic + minefield
    CONFIG = {}                 # no per-seat options: it always plays fast
    MODES = ("2p/*/classic", "2p/*/minefield")   # two-player plain board only
    says = True                 # it announces a finished game through chat

    budget = 0.15               # a fraction of the classic engines' time
    MAX_SEARCH_DEPTH = 10       # almost never reached; the clock decides
    HANG_MIN = 120              # blitz is a touch warier about loose pieces

    def __init__(self):
        super().__init__()
        self._engine = _classic.Engine()

    def choose(self, position):
        """A quick, well-ordered shallow search, then the blunder guard. The
        move is chosen for speed over depth; in a game it has already won it
        says so in the chat."""
        moves = position.legal_moves()
        if not moves:
            return None
        if _c2p is not None:
            # book FENs are 8x8, so a forced 4P cross position never matches
            # and falls straight through to the search below
            book = _c2p.lookup_book(position.fen())
            if book:
                mv = _c2p.pick_book_move(book, moves)
                if mv is not None:
                    return mv
        eng = self._engine
        eng.load(position.board)
        cross = eng.shape == CROSS          # the four-army board, not mapped 2P
        army = ARMY_OF[position.turn] if cross else (
            0 if position.turn == "w" else 1)
        m, score, scores = eng.search(army, self.budget, self.MAX_SEARCH_DEPTH)
        if m is None:
            return random.choice(moves)
        if scores:
            m = eng.guard(list(scores), scores, m, score) or m
            m = _classic.enforce_progress(eng, position, moves, scores, m,
                                          scores.get(m, score), cross)
        mv = self._external(position, moves, m, cross)
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

    @staticmethod
    def _external(position, moves, m, cross):
        """The internal packed move as the board's own Move object, or None
        when the mapped board's view and the real one disagree (a fogged
        opponent the engine could not see was standing in the way)."""
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
    # tiny self-test: python bots/fligbot_blitz.py
    from fligchess.core.board import Board
    from fligchess.core.board4 import Board4
    bot = FligBotBlitz()
    bot.configure({})
    # a legal move on the classic board
    p2 = Position(fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                  info=GameInfo(players=2, net="duel", army="w"))
    mv = bot.choose(p2)
    print(f"{bot.name} 2p picks:", mv, "legal" if mv in p2.legal_moves() else "ILLEGAL")
    # a legal move across the four-army cross
    p4 = Position(board=Board4.startpos(),
                  info=GameInfo(players=4, net="offline", army="w"))
    mv = bot.choose(p4)
    print(f"{bot.name} 4p picks:", mv, "legal" if mv in p4.legal_moves() else "ILLEGAL")
    # it finds a free mate and announces it
    im = GameInfo(players=2, net="duel", army="w")
    pm = Position(fen="6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", info=im)
    mv = bot.choose(pm)
    print(f"{bot.name} mate-in-1:", mv, "says", im.drain())
    # the blunder guard stays on: a queen onto a pawn's gun is refused
    eng = bot._engine
    eng.load(Board.from_fen("4k3/8/8/8/4p3/8/8/3QK3 w - - 0 1"))
    eng.n_live = len(eng.standing())
    eng.me = eng.turn = 0
    legal = eng.gen_legal(0)
    pull = _classic.pack(_classic._idx(6, 9), _classic._idx(6, 7))  # Qd1-d3
    hang = {m: eng.hang(m) for m in legal}
    safe = any(hang[m] < bot.HANG_MIN for m in legal if m != pull)
    keep = eng.guard(legal, {m: 0 for m in legal}, pull, 0, hang)
    print("blunder guard keeps the queen off d3:", hang[pull] >= bot.HANG_MIN
          and safe and keep != pull)
