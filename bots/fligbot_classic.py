"""fligbot classic - the bundled FligChess bot.

It exposes no per-seat options and never weakens itself: every move is its
single strongest search - iterative deepening within a fixed thinking budget
(`FligBot.budget`, overridden by the app's --budget flag) to a deep ply cap,
with full pruning, a tapered evaluation and an opening book - and then two
horizon audits that refuse a pick leaving a whole piece loose (`refine`) or
hanging the very piece the move shifted (`blunder_pass`). It always plays
to win, and it plays the two-player board: the four-army cross has its own
engine in `fligbot_classic_4p.py`.
"""

import pathlib
import random
import sys
import time

try:
    import fligchess.core
except ImportError: 
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from fligchess.core import Bot, Move, Position, WHITE, BLACK

P, N, B, R, Q, K = 1, 2, 3, 4, 5, 6          # white piece ids; +8 for black
WHITE_0, BLACK_0 = 0, 1                       # engine-side ids
MATE = 1_000_000
MATE_THRESHOLD = MATE - 10_000
MAXPLY = 64
INF = 10 ** 9

VAL = (0, 100, 320, 330, 500, 900, 20000)     # per piece id (P..K)
NV = (0, 0, 3, 3, 5, 9, 0)                    # phase weight per piece id

# the moved-piece safety pass (see `blunder_pass`): how much the opponent
# must stand to win for a capture to count as a hang, how close to the pick
# a safe rival has to be before it may replace it, and how many rivals are
# re-checked, safest first
HANG_MIN = 150                # above a pawn, so a free-pawn push is not a
                              # blunder - gambits and breaks are normal chess
EQUAL_CP = 30
VERIFY_CAP = 8                # rivals given a fresh quiescence score
VERIFIER_BUDGET = 0.5         # seconds the whole audit may take

N_DELTAS = (33, 18, -14, -31, -33, -18, 14, 31)       # 0x88 offsets
DIAG = (17, 15, -17, -15)
STRAIGHT = (16, -16, 1, -1)
KING_DELTAS = DIAG + STRAIGHT
SQ64 = [sq for sq in range(128) if not sq & 0x88]
CORNER_RIGHT = {7: 1, 0: 2, 112: 4, 120: 8}    # rook square -> castling bit

# move is packed: from | to<<8 | promo<<16 | flags<<19
F_DOUBLE, F_EP, F_CASTLE, F_PROMO = 1, 2, 4, 8


def pack(frm, to, promo=0, flags=0):
    return frm | (to << 8) | (promo << 16) | (flags << 19)


# ------------------------------------------- evaluation tables (tapered mg/pg)
# Values follow the classic "simplified evaluation function" tables, indexed
# for white from a8..h1; black mirrors through sq ^ 56.

PST_MG = {
    P: (0, 0, 0, 0, 0, 0, 0, 0,
        50, 50, 50, 50, 50, 50, 50, 50,
        10, 10, 20, 30, 30, 20, 10, 10,
        5, 5, 10, 25, 25, 10, 5, 5,
        0, 0, 0, 20, 20, 0, 0, 0,
        5, -5, -10, 0, 0, -10, -5, 5,
        5, 10, 10, -20, -20, 10, 10, 5,
        0, 0, 0, 0, 0, 0, 0, 0),
    N: (-50, -40, -30, -30, -30, -30, -40, -50,
        -40, -20, 0, 0, 0, 0, -20, -40,
        -30, 0, 10, 15, 15, 10, 0, -30,
        -30, 5, 15, 20, 20, 15, 5, -30,
        -30, 0, 15, 20, 20, 15, 0, -30,
        -30, 5, 10, 15, 15, 10, 5, -30,
        -40, -20, 0, 5, 5, 0, -20, -40,
        -50, -40, -30, -30, -30, -30, -40, -50),
    B: (-20, -10, -10, -10, -10, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 5, 10, 10, 10, 10, 5, -10,
        -10, 5, 10, 10, 10, 10, 5, -10,
        -10, 0, 10, 10, 10, 10, 0, -10,
        -10, 10, 10, 10, 10, 10, 10, -10,
        -10, 5, 0, 0, 0, 0, 5, -10,
        -20, -10, -10, -10, -10, -10, -10, -20),
    R: (0, 0, 0, 0, 0, 0, 0, 0,
        5, 10, 10, 10, 10, 10, 10, 5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        0, 0, 0, 5, 5, 0, 0, 0),
    Q: (-20, -10, -10, -5, -5, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 5, 5, 5, 0, -10,
        -5, 0, 5, 5, 5, 5, 0, -5,
        0, 0, 5, 5, 5, 5, 0, -5,
        -10, 5, 5, 5, 5, 5, 0, -10,
        -10, 0, 5, 0, 0, 0, 0, -10,
        -20, -10, -10, -5, -5, -10, -10, -20),
    K: (-30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -20, -30, -30, -40, -40, -30, -30, -20,
        -10, -20, -20, -20, -20, -20, -20, -10,
        20, 20, 0, 0, 0, 0, 20, 20,
        20, 30, 10, 0, 0, 10, 30, 20),
}
PST_PG = {
    P: (0,) * 64,
    N: PST_MG[N],
    B: PST_MG[B],
    R: (0, 0, 0, 0, 0, 0, 0, 0,
        5, 10, 10, 10, 10, 10, 10, 5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        0, 0, 0, 0, 0, 0, 0, 0),
    Q: PST_MG[Q],
    K: (-50, -40, -30, -20, -20, -30, -40, -50,
        -30, -20, -10, 0, 0, -10, -20, -30,
        -30, -10, 20, 30, 30, 20, -10, -30,
        -30, -10, 30, 40, 40, 30, -10, -30,
        -30, -10, 30, 40, 40, 30, -10, -30,
        -30, -10, 20, 30, 30, 20, -10, -30,
        -30, -30, 0, 0, 0, 0, -30, -30,
        -50, -30, -30, -30, -30, -30, -30, -50),
}
# passed-pawn bonus by own rank (0 = own 2nd rank), indexed r = 0..7
PASS_MG = (0, 5, 10, 20, 35, 60, 100, 0)
PASS_PG = (0, 8, 16, 30, 55, 90, 140, 0)

# ------------------------------------------------------------------ zobrist

_rng = random.Random(0x5A17FACE)
ZO_PIECE = [[_rng.getrandbits(64) for _ in range(128)] for _ in range(15)]
ZO_SIDE = _rng.getrandbits(64)
_CASTLE_BITS = [_rng.getrandbits(64) for _ in range(4)]
CASTLE_KEY = [0] * 16
for _c in range(16):
    _k = 0
    for _i in range(4):
        if _c & (1 << _i):
            _k ^= _CASTLE_BITS[_i]
    CASTLE_KEY[_c] = _k
EP_KEY = [_rng.getrandbits(64) for _ in range(8)]


def _ep_key(sq):
    return 0 if sq < 0 else EP_KEY[sq & 7]


# mobility: only the cheap knight leap is scored - ray-based mobility for
# bishops/rooks/queens costs far too many nodes/second for a pure-python
# search to be worth it, and rook/queen open files are already judged below.
MOBILE = {N: (N_DELTAS, False)}
MOBW = {N: 2}

# evaluation terms the piece-square tables cannot express: a piece standing
# where an enemy pawn can chase it, a slider with its exits being closed, a
# king bolted into its own pawn cage and a pawn backed by its neighbour
HARASS = 10
TRAPPED = 45
CAGE = 20
SUPPORT = 5


def _exits(b, sq, t, own):
    """Squares a bishop or rook on sq may flee to - the first step in each
    of its directions that is on the board and not blocked by its own side.
    A pawn storm that takes these away boxes the piece in."""
    dirs = DIAG if t == B else STRAIGHT
    n = 0
    for d in dirs:
        a = sq + d
        if a & 0x88:
            continue
        v = b[a]
        if not v or (v & 8) != own:
            n += 1
    return n


def _mobility(b, sq, own, dirs, ray):
    """Squares a piece on sq may influence (empty or enemy-occupied)."""
    n = 0
    if ray:
        for d in dirs:
            t = sq + d
            while not t & 0x88:
                v = b[t]
                if v:
                    if v & 8 != own:
                        n += 1
                    break
                n += 1
                t += d
    else:
        for d in dirs:
            t = sq + d
            if not t & 0x88 and (b[t] & 8) != own:
                n += 1
    return n


# ------------------------------------------------------------- opening book
# A small hand-authored repertoire of mainlines, walked from the start
# position at import so every key is a real, reachable FEN. A key is the four
# placement/turn/castling/en-passant fields (the move counters are dropped).
# It only ever matches ordinary chess, so the fog/minefield variants - whose
# blurred or blasted boards produce none of these FENs - fall through to the
# full search, where a memorised opening would be wrong anyway.

_BOOK_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
# one coordinate token per ply (long algebraic, e1g1 castles, e7e8q promotes);
# shared prefixes merge into the same key, so a move listed under two lines
# is offered as two equally-weighted choices for variety
_BOOK_LINES = [
    # 1.e4 e5 - Ruy Lopez (Closed), Italian (Giuoco Piano), Scotch, Four Knights
    ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1",
     "f8e7", "b1c3", "b7b5"],
    ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3", "g8f6", "d2d3",
     "d7d6", "e1g1", "e8g8"],
    ["e2e4", "e7e5", "g1f3", "b8c6", "d2d4", "e5d4", "f3d4", "g8f6", "b1c3",
     "f8b4", "d4c6", "b7c6", "c1g5", "d7d5"],
    ["e2e4", "e7e5", "g1f3", "b8c6", "b1c3", "g8f6", "f1b5", "f8b4", "e1g1",
     "b4c3", "d2d3", "d7d6"],
    # 1.e4 - semi-open games
    ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3",
     "a7a6", "c1e3", "e7e5", "d4b3", "c8e6"],           # Najdorf
    ["e2e4", "c7c5", "g1f3", "b8c6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3",
     "d7d6", "c1g5", "e7e6"],                           # Classical
    ["e2e4", "c7c5", "g1f3", "e7e6", "d2d4", "c5d4", "f3d4", "a7a6", "b1c3",
     "d8c7", "f1d3", "g8f6"],                           # Taimanov
    ["e2e4", "e7e6", "d2d4", "d7d5", "b1c3", "f8b4", "e4e5", "c7c5", "a2a3",
     "b4c3", "b2c3", "d8c7"],                           # French Winawer
    ["e2e4", "e7e6", "d2d4", "d7d5", "e4e5", "c7c5", "c2c3", "b8c6", "g1f3",
     "d8b6"],                                           # French Advance
    ["e2e4", "c7c6", "d2d4", "d7d5", "b1c3", "d5e4", "c3e4", "c8f5", "e4g3",
     "f5g6", "h2h4", "h7h6"],                           # Caro-Kann
    ["e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5a5", "d2d4", "g8f6", "g1f3",
     "c8f5"],                                           # Scandinavian
    # 1.d4 d5 - Queen's Gambit family
    ["d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "c1g5", "f8e7", "e2e3",
     "e8g8", "g1f3", "b8d7"],                           # QGD Orthodox
    ["d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "c4d5", "e6d5", "c1g5",
     "c7c6", "e2e3", "f8e7"],                           # Exchange
    ["d2d4", "d7d5", "c2c4", "c7c6", "g1f3", "g8f6", "b1c3", "d5c4", "a2a4",
     "c8f5", "e2e3", "e7e6"],                           # Slav
    ["d2d4", "d7d5", "c2c4", "d5c4", "g1f3", "g8f6", "b1c3", "a7a6", "e2e4",
     "b7b5", "e4e5", "f6d5", "c3b5", "a6b5"],           # QGA
    # 1.d4 Nf6 - Indian games
    ["d2d4", "g8f6", "c2c4", "e7e6", "b1c3", "f8b4", "e2e3", "c7c5", "f1d3",
     "b8c6", "g1f3", "d7d5"],                           # Nimzo-Indian
    ["d2d4", "g8f6", "c2c4", "g7g6", "b1c3", "d7d5", "c4d5", "f6d5", "e2e4",
     "d5c3", "b2c3", "f8g7"],                           # Grunfeld
    ["d2d4", "g8f6", "c2c4", "g7g6", "b1c3", "f8g7", "e2e4", "d7d6", "g1f3",
     "e8g8", "f1e2", "b8c6", "e1g1", "a7a6"],           # King's Indian
    ["d2d4", "g8f6", "c2c4", "e7e6", "g1f3", "d7d5", "g2g3", "f8e7", "f1g2",
     "e8g8", "e1g1", "d5c4"],                           # Catalan
    # flank openings
    ["c2c4", "e7e5", "b1c3", "g8f6", "g1f3", "b8c6", "g2g3", "d7d5", "c4d5",
     "f6d5"],                                           # English
    ["c2c4", "g8f6", "b1c3", "d7d5", "c4d5", "f6d5", "g2g3", "e7e5", "f1g2",
     "c7c6"],                                            # English vs Indian
    ["g1f3", "d7d5", "c2c4", "e7e6", "g2g3", "g8f6", "f1g2", "f8e7", "e1g1",
     "e8g8"],                                            # Reti
    # more black defences and openings, so the search starts from a sound
    # middlegame rather than an invented first move
    ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6", "b1c3",
     "g7g6", "f1e2", "f8g7", "e1g1", "e8g8"],           # Sicilian Dragon
    ["e2e4", "d7d6", "d2d4", "g8f6", "b1c3", "g7g6", "g1f3", "f8g7", "f1e2",
     "e8g8"],                                            # Pirc
    ["e2e4", "c7c6", "d2d4", "d7d5", "e4e5", "c8f5", "g1f3", "e7e6", "f1e2",
     "g8f6"],                                            # Caro-Kann Advance
    ["e2e4", "e7e5", "f2f4", "e5f4", "g1f3", "d7d5", "e4d5", "g8f6", "f1e2",
     "f6d5", "e1g1", "c7c5"],                           # King's Gambit
    ["d2d4", "f7f5", "c2c4", "g8f6", "g2g3", "e7e6", "f1g2", "d7d5", "g1f3",
     "f8e7", "e1g1", "e8g8"],                            # Dutch
    ["d2d4", "g8f6", "g1f3", "d7d5", "c1f4", "e7e6", "e2e3", "f8d6", "f4d6",
     "d8d6", "b1c3", "e8g8"],                            # London
]


def _book_key(fen):
    parts = fen.split()
    return " ".join(parts[:4])


def _book_move(token):
    """'e2e4' / 'e7e8q' -> a Move, using 0-indexed (file, rank) squares."""
    frm, to = token[0:2], token[2:4]
    promo = token[4] if len(token) > 4 else None
    return Move((ord(frm[0]) - 97, int(frm[1]) - 1),
                (ord(to[0]) - 97, int(to[1]) - 1), promo)


def _build_book():
    book = {}
    root = Position(fen=_BOOK_START)
    for line in _BOOK_LINES:
        pos = root
        for token in line:
            want = _book_move(token)
            legal = next((m for m in pos.legal_moves() if m == want), None)
            if legal is None:
                break                     # a bad token only truncates its line
            entries = book.setdefault(_book_key(pos.fen()), [])
            if not any(m == legal for m, _w in entries):
                entries.append((legal, 1))
            pos = pos.make(legal)
    return book


_BOOK = _build_book()


def lookup_book(fen):
    """The book entries ((Move, weight) list) for this position, or None."""
    return _BOOK.get(_book_key(fen))


def pick_book_move(entries, moves):
    """A weighted-random book move that is genuinely legal here; None if the
    book line no longer fits, so the caller falls through to the search."""
    legal = [(mv, w) for mv, w in entries if mv in moves]
    if not legal:
        return None
    total = sum(w for _mv, w in legal)
    roll = random.uniform(0, total)
    upto = 0
    for mv, w in legal:
        upto += w
        if roll <= upto:
            return mv
    return legal[-1][0]


class Engine:
    """0x88 board + alpha-beta search. Squares: rank*16 + file, a1 = 0."""

    def __init__(self):
        self.b = [0] * 128
        self.kings = [0, 0]
        self.npm = [0, 0]            # non-pawn/king phase weight per side
        self.tt = {}
        self.hist = [[0] * 128 for _ in range(128)]
        self.killers = [[0, 0] for _ in range(MAXPLY + 1)]
        self.counter = {}              # opponent's move -> our best refutation
        self.pv = [0] * (MAXPLY + 2)   # the move that led to each ply
        self.nodes = 0
        self.deadline = 0.0
        self.last_depth = 0
        self.reps = {}                    # zobrist -> count on current path
        self.root_scores = {}
        self.second = None

    # ------------------------------------------------------------- setup

    def load(self, fen):
        b = self.b
        for i in range(128):
            b[i] = 0
        rows, turn, cast, ep = fen.split()[:4]
        rank = 7
        file_ = 0
        for ch in rows:
            if ch.isdigit():
                file_ += int(ch)
            elif ch == "/":
                rank -= 1
                file_ = 0
            else:
                pc = {"p": 1, "n": 2, "b": 3, "r": 4, "q": 5, "k": 6}[ch.lower()]
                if ch.islower():
                    pc += 8
                b[rank * 16 + file_] = pc
                if pc & 7 == K:
                    self.kings[0 if pc < 8 else 1] = rank * 16 + file_
                file_ += 1
        self.side = 0 if turn == "w" else 1
        self.castle = 0
        for ch, bit in (("K", 1), ("Q", 2), ("k", 4), ("q", 8)):
            if ch in cast:
                self.castle |= bit
        self.ep = -1 if ep == "-" else ("abcdefgh".index(ep[0])
                                        + 16 * (int(ep[1]) - 1))
        self.halfm = int(fen.split()[4])
        self.npm = [0, 0]
        z = 0
        for sq in SQ64:
            p = b[sq]
            if p:
                z ^= ZO_PIECE[p][sq]
                t = p & 7
                if t not in (P, K):
                    self.npm[0 if p < 8 else 1] += NV[t]
        z ^= CASTLE_KEY[self.castle] ^ _ep_key(self.ep)
        if self.side:
            z ^= ZO_SIDE
        self.z = z
        self.reps = {z: 1}

    def _push_rep(self, z):
        self.reps[z] = self.reps.get(z, 0) + 1

    def _pop_rep(self, z):
        r = self.reps[z]
        if r > 1:
            self.reps[z] = r - 1
        else:
            del self.reps[z]

    # ------------------------------------------------------ make / unmake

    def make(self, m):
        frm = m & 255
        to = (m >> 8) & 255
        promo = (m >> 16) & 7
        flags = m >> 19
        b = self.b
        me = self.side
        enemy = me ^ 1
        piece = b[frm]
        t = piece & 7
        vsq = -1
        if flags & F_EP:
            vsq = to - 16 if me == 0 else to + 16
        cap = b[vsq] if vsq >= 0 else b[to]
        undo = (piece, cap, vsq, self.z, self.castle, self.ep,
                self.halfm, self.kings[me], self.npm[me], self.npm[enemy])

        b[frm] = 0
        if vsq >= 0:
            b[vsq] = 0
        moved = piece if not promo else promo | (piece & 8)
        b[to] = moved

        z = self.z ^ ZO_PIECE[piece][frm] ^ ZO_PIECE[moved][to] ^ ZO_SIDE
        if cap:
            z ^= ZO_PIECE[cap][vsq if vsq >= 0 else to]

        if t == K:
            self.kings[me] = to
            if abs((to & 7) - (frm & 7)) == 2:          # castling: rook hop
                if (to & 7) == 6:
                    rf, rt = frm + 3, frm + 1
                else:
                    rf, rt = frm - 4, frm - 1
                rook = b[rf]
                b[rf] = 0
                b[rt] = rook
                z ^= ZO_PIECE[rook][rf] ^ ZO_PIECE[rook][rt]

        newc = self.castle
        if t == K:
            newc &= ~3 if me == 0 else ~12
        newc &= ~CORNER_RIGHT.get(frm, 0) & ~CORNER_RIGHT.get(to, 0)
        if newc != self.castle:
            z ^= CASTLE_KEY[self.castle] ^ CASTLE_KEY[newc]
        newep = (frm + to) // 2 if flags & F_DOUBLE else -1
        if newep != self.ep:
            z ^= _ep_key(self.ep) ^ _ep_key(newep)

        self.castle = newc
        self.ep = newep
        self.halfm = 0 if (t == P or cap) else self.halfm + 1
        if t not in (P, K):
            self.npm[me] -= NV[t]
        if promo:
            self.npm[me] += NV[promo]
        if cap and (cap & 7) not in (P, K):
            self.npm[enemy] -= NV[cap & 7]
        self.z = z
        self._push_rep(z)
        self.side = enemy
        return undo

    def unmake(self, m, undo):
        frm = m & 255
        to = (m >> 8) & 255
        flags = m >> 19
        piece, cap, vsq, z, castle, ep, halfm, king_sq, npm_me, npm_en = undo
        b = self.b
        me = self.side ^ 1
        self._pop_rep(self.z)
        b[frm] = piece
        if vsq >= 0:
            b[vsq] = cap
            b[to] = 0
        else:
            b[to] = cap
        if (piece & 7) == K:
            if abs((to & 7) - (frm & 7)) == 2:
                if (to & 7) == 6:
                    rf, rt = frm + 3, frm + 1
                else:
                    rf, rt = frm - 4, frm - 1
                b[rf] = b[rt]
                b[rt] = 0
            self.kings[me] = king_sq
        self.side = me
        self.z = z
        self.castle = castle
        self.ep = ep
        self.halfm = halfm
        self.npm[me] = npm_me
        self.npm[me ^ 1] = npm_en

    # ------------------------------------------------------- state snapshots
    # an abandoned search unwinds through frames that still have their move
    # applied, so anything that can be interrupted restores from one of these

    def snapshot(self):
        return (self.b[:], self.side, tuple(self.kings), self.castle,
                self.ep, self.halfm, tuple(self.npm), self.z)

    def restore(self, snap):
        (self.b, self.side, kings, self.castle, self.ep, self.halfm,
         npm, self.z) = snap
        self.kings = list(kings)
        self.npm = list(npm)
        self.reps = {self.z: 1}

    # ------------------------------------------------------------ attacks

    def attacked(self, sq, by):
        b = self.b
        o = 8 * by
        pw = 1 + o
        for d in ((-15, -17) if by == 0 else (15, 17)):
            t = sq + d
            if not t & 0x88 and b[t] == pw:
                return True
        for d in N_DELTAS:
            t = sq + d
            if not t & 0x88 and b[t] == 2 + o:
                return True
        for d in KING_DELTAS:
            t = sq + d
            if not t & 0x88 and b[t] == 6 + o:
                return True
        for d in DIAG:
            t = sq + d
            while not t & 0x88:
                p = b[t]
                if p:
                    if p & 8 == o and (p & 7) in (B, Q):
                        return True
                    break
                t += d
        for d in STRAIGHT:
            t = sq + d
            while not t & 0x88:
                p = b[t]
                if p:
                    if p & 8 == o and (p & 7) in (R, Q):
                        return True
                    break
                t += d
        return False

    # ------------------------------------------------------- move gen

    def gen(self, caps=False):
        b = self.b
        side = self.side
        o = 8 * side
        fwd = 16 if side == 0 else -16
        start_rank = 1 if side == 0 else 6
        # a pawn promotes when it steps ONTO the last rank, so the rank it
        # stands on just before that is one closer to its own side: for
        # black that is rank 1 (b2-style), not 0 - getting this wrong means
        # black simply never promotes
        promo_rank = 6 if side == 0 else 1
        ep = self.ep
        moves = []
        for sq in SQ64:
            p = b[sq]
            if not p or (p & 8) != o:
                continue
            t = p & 7
            r = sq >> 4
            if t == P:
                a = sq + fwd
                if not a & 0x88 and not b[a]:
                    if r == promo_rank:
                        # a queening push is tactical even though it
                        # captures nothing, so qsearch must see it too;
                        # there only the queen promo is worth the branch
                        if caps:
                            moves.append(pack(sq, a, Q))
                        else:
                            for pr in (Q, R, B, N):
                                moves.append(pack(sq, a, pr))
                    elif not caps:
                        moves.append(pack(sq, a))
                        if r == start_rank and not b[a + fwd]:
                            moves.append(pack(sq, a + fwd, 0, F_DOUBLE))
                for d in (fwd + 1, fwd - 1):
                    a = sq + d
                    if a & 0x88:
                        continue
                    v = b[a]
                    if v and (v & 8) != o:
                        if r == promo_rank:
                            for pr in (Q, R, B, N):
                                moves.append(pack(sq, a, pr))
                        else:
                            moves.append(pack(sq, a))
                    elif a == ep and not v:
                        moves.append(pack(sq, a, 0, F_EP))
            elif t == N:
                for d in N_DELTAS:
                    a = sq + d
                    if a & 0x88:
                        continue
                    v = b[a]
                    if not v:
                        if not caps:
                            moves.append(pack(sq, a))
                    elif (v & 8) != o:
                        moves.append(pack(sq, a))
            elif t == K:
                for d in KING_DELTAS:
                    a = sq + d
                    if a & 0x88:
                        continue
                    v = b[a]
                    if not v:
                        if not caps:
                            moves.append(pack(sq, a))
                    elif (v & 8) != o:
                        moves.append(pack(sq, a))
                if not caps:
                    self._castle(moves)
            else:
                dirs = DIAG if t == B else STRAIGHT if t == R else DIAG + STRAIGHT
                for d in dirs:
                    a = sq + d
                    while not a & 0x88:
                        v = b[a]
                        if not v:
                            if not caps:
                                moves.append(pack(sq, a))
                        else:
                            if (v & 8) != o:
                                moves.append(pack(sq, a))
                            break
                        a += d
        return moves

    def _castle(self, moves):
        side = self.side
        base = 0 if side == 0 else 112
        ks_bit, qs_bit = (1, 2) if side == 0 else (4, 8)
        enemy = side ^ 1
        king_sq = 4 + base
        if self.b[king_sq] != K + 8 * side:
            return
        rook = R + 8 * side
        if (self.castle & ks_bit and not self.b[5 + base]
                and not self.b[6 + base] and self.b[7 + base] == rook
                and not self.attacked(king_sq, enemy)
                and not self.attacked(5 + base, enemy)
                and not self.attacked(6 + base, enemy)):
            moves.append(pack(king_sq, 6 + base, 0, F_CASTLE))
        if (self.castle & qs_bit and not self.b[1 + base]
                and not self.b[2 + base] and not self.b[3 + base]
                and self.b[base] == rook
                and not self.attacked(king_sq, enemy)
                and not self.attacked(3 + base, enemy)
                and not self.attacked(2 + base, enemy)):
            moves.append(pack(king_sq, 2 + base, 0, F_CASTLE))

    def gen_legal(self, caps=False):
        out = []
        for m in self.gen(caps):
            u = self.make(m)
            me = self.side ^ 1
            if not self.attacked(self.kings[me], self.side):
                out.append(m)
            self.unmake(m, u)
        return out

    def perft(self, depth):
        """move-count self-test: compares make/unmake + legality against
        published perft numbers; used by the bot test suite."""
        if depth == 0:
            return 1
        n = 0
        for m in self.gen_legal():
            u = self.make(m)
            n += self.perft(depth - 1)
            self.unmake(m, u)
        return n

    # ------------------------------------------------------- static exchange

    def _pop_attacker(self, sq, by):
        """Move the cheapest piece of colour `by` attacking sq onto sq
        (vacating its origin) and return (value, src, piece); the caller
        must restore b[src] = piece. Removing the attacker naturally
        exposes the sliders behind it, so x-rays cost no extra code."""
        b = self.b
        o = 8 * by
        for d in ((-15, -17) if by == 0 else (15, 17)):
            t = sq + d
            if not t & 0x88 and b[t] == 1 + o:
                p = b[t]
                b[t] = 0
                b[sq] = p
                return VAL[P], t, p
        for d in N_DELTAS:
            t = sq + d
            if not t & 0x88 and b[t] == 2 + o:
                p = b[t]
                b[t] = 0
                b[sq] = p
                return VAL[N], t, p
        best = None
        for dirs, types in ((DIAG, (B, Q)), (STRAIGHT, (R, Q))):
            for d in dirs:
                t = sq + d
                while not t & 0x88:
                    p = b[t]
                    if p:
                        if (p & 8) == o and (p & 7) in types:
                            v = VAL[p & 7]
                            if best is None or v < best[0]:
                                best = (v, t, p)
                        break
                    t += d
        if best is not None:
            b[best[1]] = 0
            b[sq] = best[2]
            return best
        for d in KING_DELTAS:
            t = sq + d
            if not t & 0x88 and b[t] == 6 + o:
                p = b[t]
                b[t] = 0
                b[sq] = p
                return VAL[K], t, p
        return None

    def see(self, m):
        """Full static exchange evaluation: pop cheapest alternating
        attackers after the initial capture and fold the gain list.
        Approximations: kings may "capture" inside the exchange and
        pinned defenders are not filtered - order and pruning only need
        the sign of the result, and both err loudly, not silently."""
        b = self.b
        frm = m & 255
        to = (m >> 8) & 255
        promo = (m >> 16) & 7
        victim = b[to]
        ep_sq = -1
        if not victim and (m >> 19) & F_EP:
            ep_sq = to - 16 if self.side == 0 else to + 16
            victim = b[ep_sq]
        if not victim:
            return VAL[promo] - VAL[P] if promo else 0
        mover = b[frm]
        # NOTE: for ep captures b[to] itself is empty - saving the victim
        # there instead of the square's real contents ghosted a pawn back
        # onto the board after every ep exchange evaluation
        saved = [(frm, mover), (to, b[to])]
        if ep_sq >= 0:
            saved.append((ep_sq, b[ep_sq]))
            b[ep_sq] = 0
        occ = (promo + 8 * self.side) if promo else mover
        b[frm] = 0
        b[to] = occ
        gains = [VAL[victim & 7]]
        oc = VAL[occ & 7]
        turn = self.side ^ 1
        while len(gains) < 7:
            hit = self._pop_attacker(to, turn)
            if hit is None:
                break
            gains.append(oc)
            oc = hit[0]
            saved.append((hit[1], hit[2]))
            turn ^= 1
        for s, p in saved:
            b[s] = p
        net = gains[0]
        sign = -1
        for g in gains[1:]:
            net += sign * g
            sign = -sign
        return net

    # ------------------------------------------------------- material safety
    # the search horizon at game budget is only ~5 plies, so the engine can
    # still walk into boards where a piece hangs one ply past it. these
    # helpers audit a finished position directly and are used once per move
    # at the root, never inside the hot search loop

    def attackers_of(self, sq, by):
        """Squares of pieces of colour `by` attacking sq; no early exit."""
        b = self.b
        o = 8 * by
        out = []
        for d in ((-15, -17) if by == 0 else (15, 17)):
            t = sq + d
            if not t & 0x88 and b[t] == 1 + o:
                out.append(t)
        for d in N_DELTAS:
            t = sq + d
            if not t & 0x88 and b[t] == 2 + o:
                out.append(t)
        for d in KING_DELTAS:
            t = sq + d
            if not t & 0x88 and b[t] == 6 + o:
                out.append(t)
        for d in DIAG:
            t = sq + d
            while not t & 0x88:
                p = b[t]
                if p:
                    if (p & 8) == o and (p & 7) in (B, Q):
                        out.append(t)
                    break
                t += d
        for d in STRAIGHT:
            t = sq + d
            while not t & 0x88:
                p = b[t]
                if p:
                    if (p & 8) == o and (p & 7) in (R, Q):
                        out.append(t)
                    break
                t += d
        return out

    def loose_loss(self, side, pin_aware=True):
        """Value of `side`'s material (king excluded) attacked more times
        than defended. With pin_aware a defender only counts if lifting it
        off the board does not expose its own king - a pinned defender is
        worth nothing, which is exactly the 'it was defended... by the
        wrong piece' class of hang."""
        b = self.b
        opp = side ^ 1
        king = self.kings[side]
        total = 0
        for sq in SQ64:
            p = b[sq]
            if not p or (p & 7) == K or (p & 8) != 8 * side:
                continue
            na = self.attackers_of(sq, opp)
            if not na:
                continue
            dns = self.attackers_of(sq, side)
            nd = len(dns)
            if pin_aware and nd:
                for d in dns:
                    if (b[d] & 7) == K:
                        continue
                    keep = b[d]
                    b[d] = 0
                    pinned = self.attacked(king, opp)
                    b[d] = keep
                    if pinned:
                        nd -= 1
            if len(na) > nd:
                total += VAL[p & 7]
        return total

    def refine(self, legal, scores, best, best_score):
        """Safety net for the shallow horizon: it only intervenes when the
        move the search picked actually leaves a whole piece en prise, and
        then rescues into a near-best move with less loose material. Root
        scores for non-best moves are window bounds, not exact values - a
        bound below best-150 proves the true score is too, so the candidate
        filter is sound; an earlier version RANKED by those bounds and lost
        every game it touched."""
        if best is None or abs(best_score) >= MATE_THRESHOLD:
            return best
        u = self.make(best)
        loss = self.loose_loss(self.side ^ 1)
        self.unmake(best, u)
        if loss < 250:                     # nothing real is hanging
            return best
        base = self.loose_loss(self.side ^ 1, pin_aware=False)
        ranked = []
        for m in legal:
            if m == best or scores.get(m, -INF) < best_score - 150:
                continue
            u = self.make(m)
            l2 = self.loose_loss(self.side ^ 1)
            g2 = self.loose_loss(self.side, pin_aware=False)
            self.unmake(m, u)
            ranked.append((l2, -max(0, g2 - base), m))
        if not ranked:
            return best
        ranked.sort()
        return ranked[0][2] if ranked[0][0] < loss else best

    # ------------------------------------------------- moved-piece safety
    # `refine` audits the whole board once the move is on it. This pass asks
    # the single question it cannot: can the piece the move *displaced* be
    # taken right now? That is the difference between "something is loose"
    # and "I planted my rook on a square every pawn can eat".

    def hanging(self, m):
        """Material the opponent wins by capturing the piece `m` has just
        put on its destination square; 0 when nothing can take it there.
        SEE scores the whole exchange, so a recapture behind the first grab
        is already folded in, and kings are exempt - they are never taken.
        Called with `m` already made, so the replies are the opponent's."""
        to = (m >> 8) & 255
        piece = self.b[to]
        if not piece or (piece & 7) == K:
            return 0
        worst = 0
        for r in self.gen(caps=True):
            if ((r >> 8) & 255) != to:
                continue
            gain = self.see(r)
            if gain > worst:
                worst = gain
        return worst

    def probe(self, m):
        """A fresh wide-window score for one root move: play it and let the
        quiescence search settle every answer that captures anything. Root
        scores out of the iteration are aspiration bounds - an upper bound
        for every move that failed low - so a rival has to be re-scored
        before it may replace the pick, which is exactly the mistake an
        earlier version of this pass made by ranking on those bounds."""
        u = self.make(m)
        try:
            return -self.qsearch(-INF, INF, 1)
        finally:
            self.unmake(m, u)

    def blunder_pass(self, legal, scores, best, best_score):
        """Trade the pick in for a rival that keeps its own piece safe, when
        a re-search calls the two of them equal. Deliberately narrow: the
        swap may cost at most `EQUAL_CP`, a pick that gives check is left
        alone - a sacrifice is meant to hang its piece - and mate lines are
        never touched."""
        if best is None or abs(best_score) >= MATE_THRESHOLD:
            return best
        u = self.make(best)
        checking = self.attacked(self.kings[self.side], self.side ^ 1)
        threat = self.hanging(best)
        self.unmake(best, u)
        if threat < HANG_MIN or checking:       # it holds its ground
            return best
        rivals = []
        for m in legal:
            # a bound well under the pick's score is proof the move is worse;
            # a bound near it is not proof of anything, which is why the
            # survivors below are re-searched rather than trusted
            if m == best or scores.get(m, -INF) < best_score - 150:
                continue
            u = self.make(m)
            t2 = self.hanging(m)
            self.unmake(m, u)
            if t2 < threat:
                rivals.append((t2, -scores.get(m, -INF), m))
        if not rivals:
            return best
        rivals.sort()
        snap = self.snapshot()
        saved, self.deadline = (self.deadline,
                                time.perf_counter() + VERIFIER_BUDGET)
        try:
            base = self.probe(best)
            for _t2, _neg, m in rivals[:VERIFY_CAP]:
                if self.probe(m) >= base - EQUAL_CP:
                    return m                  # safest rival still worth the move
        except TimeoutError:
            self.restore(snap)
        finally:
            self.deadline = saved
        return best

    # -------------------------------------------------------- evaluation

    def evaluate(self):
        b = self.b
        mg = pg = 0
        phase = 0
        pawn_files = ([[] for _ in range(8)], [[] for _ in range(8)])
        bishops = [0, 0]
        heavy = [0, 0]
        nonpawns = [0, 0]
        seventh = [0, 0]
        rook_file = [[], []]
        pw_color = [[0, 0], [0, 0]]     # own pawns on each square colour
        bish_color = [[], []]           # colour parity of each side's bishops
        for sq in SQ64:
            p = b[sq]
            if not p:
                continue
            white = p < 8
            c = 0 if white else 1
            t = p & 7
            idx = (7 - (sq >> 4)) * 8 + (sq & 7) if white else (sq >> 4) * 8 + (7 - (sq & 7))
            v = VAL[t] + PST_MG[t][idx]
            w = VAL[t] + PST_PG[t][idx]
            f, r = sq & 7, sq >> 4
            if t == P:
                pawn_files[c][f].append(r)
                pw_color[c][(f + r) & 1] += 1
            elif t == B:
                bishops[c] += 1
                bish_color[c].append((f + r) & 1)
            elif t in (R, Q):
                rook_file[c].append(f)
                heavy[c] += 1
                if r == (6 if white else 1):
                    seventh[c] += 1
            if t not in (P, K):
                nonpawns[c] += 1
                phase += NV[t]
            if white:
                mg += v
                pg += w
            else:
                mg -= v
                pg -= w
            spec = MOBILE.get(t)
            if spec:
                cnt = _mobility(b, sq, p & 8, spec[0], spec[1])
                val = MOBW[t] * cnt
                if white:
                    mg += val
                    pg += val // 2
                else:
                    mg -= val
                    pg -= val // 2
            if t == N:
                # outpost: a knight on the 5th/6th/7th, pawn-supported and
                # beyond the reach of any enemy pawn, cannot be driven off
                rel_r = r if white else 7 - r
                if rel_r >= 4:
                    own_p = P if white else P + 8
                    foe_p = P + 8 if white else P
                    sr = r - 1 if white else r + 1
                    supported = (0 <= sr < 8 and any(
                        b[sr * 16 + g] == own_p
                        for g in (f - 1, f + 1) if 0 <= g < 8))
                    cr = r + 1 if white else r - 1
                    chased = (0 <= cr < 8 and any(
                        b[cr * 16 + g] == foe_p
                        for g in (f - 1, f + 1) if 0 <= g < 8))
                    if supported and not chased:
                        if white:
                            mg += 15
                            pg += 6
                        else:
                            mg -= 15
                            pg -= 6
            if t in (N, B, R, Q):
                # a piece in the teeth of enemy pawns with no pawn of our own
                # behind it is chased at a tempo; two of them, with its exits
                # shutting, is not annoyance any more - it is being trapped.
                # Attacking and defending pawns stand on the very same two
                # squares either side of it, so one pass scores both
                pw = 8 * c
                ds = (-15, -17) if white else (15, 17)
                hits = backs = 0
                for d in ds:
                    a = sq + d
                    if a & 0x88:
                        continue
                    q = b[a]
                    if q == 1 + pw:
                        backs += 1
                    elif q == 9 - pw:
                        hits += 1
                if hits and not backs:
                    if white:
                        mg -= HARASS
                        pg -= HARASS // 2
                    else:
                        mg += HARASS
                        pg += HARASS // 2
                    if t in (B, R) and hits > 1 and _exits(b, sq, t, pw) <= 1:
                        if white:
                            mg -= TRAPPED
                            pg -= TRAPPED // 2
                        else:
                            mg += TRAPPED
                            pg += TRAPPED // 2
        if phase > 24:
            phase = 24
        if bishops[0] >= 2:
            mg += 30
            pg += 16
        if bishops[1] >= 2:
            mg -= 30
            pg -= 16

        # a bishop hemmed in by its own pawns on its colour is a bad one -
        # it bites on granite and cannot reach either wing
        for c in (0, 1):
            sign = 1 if c == 0 else -1
            for parity in bish_color[c]:
                n = pw_color[c][parity]
                if n >= 2:
                    mg -= sign * 7 * (n - 1)
                    pg -= sign * 5 * (n - 1)

        # pawn structure + open files
        for c in (0, 1):
            sign = 1 if c == 0 else -1
            own = pawn_files[c]
            foe = pawn_files[1 - c]
            for f in range(8):
                ranks = own[f]
                if len(ranks) > 1:
                    mg -= sign * 10 * (len(ranks) - 1)
                    pg -= sign * 12 * (len(ranks) - 1)
                if not any(own[g] for g in (f - 1, f + 1) if 0 <= g < 8):
                    for _ in ranks:
                        mg -= sign * 12
                        pg -= sign * 8
                adj = [g for g in (f - 1, f + 1) if 0 <= g < 8]
                neigh = [x for g in adj for x in own[g]]
                for r in ranks:
                    # a pawn backed by a neighbour is a chain rung: taking it
                    # costs the attacker a pawn on the spot, so the whole
                    # structure stands on these pairs
                    if any((r - 1 if c == 0 else r + 1) in own[g]
                           for g in adj):
                        mg += sign * SUPPORT
                        pg += sign * SUPPORT // 2
                    passed = True
                    for g in (f - 1, f, f + 1):
                        if 0 <= g < 8:
                            for x in foe[g]:
                                if (x > r) if c == 0 else (x < r):
                                    passed = False
                                    break
                        if not passed:
                            break
                    if passed:
                        rr = r if c == 0 else 7 - r
                        mg += sign * PASS_MG[rr]
                        pg += sign * PASS_PG[rr]
                        # graded escort bonus: a friendly pawn right behind
                        # lets the passer roll without any piece help; one
                        # level with it still backs the next push up
                        behind = r - 1 if c == 0 else r + 1
                        if behind in neigh:
                            mg += sign * 6
                            pg += sign * 12
                        elif r in neigh:
                            mg += sign * 4
                            pg += sign * 8
                    elif neigh and all((x > r) if c == 0 else (x < r)
                                       for x in neigh):
                        # backward: adjacent pawns exist but every one of
                        # them sits in FRONT of this one, so nothing can
                        # ever escort it forward - a lasting weakness
                        mg -= sign * 10
                        pg -= sign * 12
            for f in rook_file[c]:
                if not own[f] and not foe[f]:
                    mg += sign * 16
                    pg += sign * 6
                elif not own[f]:
                    mg += sign * 8
                    pg += sign * 4
            if seventh[c]:
                mg += sign * 14 * seventh[c]

        # king shield (middlegame only)
        for c in (0, 1):
            sign = 1 if c == 0 else -1
            ksq = self.kings[c]
            kf, kr = ksq & 7, ksq >> 4
            if phase > 8:
                for g in (kf - 1, kf, kf + 1):
                    if 0 <= g < 8:
                        front = kr + 1 if c == 0 else kr - 1
                        if not (0 <= front < 8 and front in pawn_files[c][g]):
                            mg -= sign * 12
                        front2 = kr + 2 if c == 0 else kr - 2
                        if not (0 <= front2 < 8 and front2 in pawn_files[c][g]):
                            mg -= sign * 5

        # back-rank cage: a king on its home rank behind all three front
        # pawns, with an enemy rook or queen still on the board, is one
        # deflection from mate - far past the search horizon, so price it
        if phase > 8:
            for c in (0, 1):
                sign = 1 if c == 0 else -1
                home = 0 if c == 0 else 7
                ksq = self.kings[c]
                if (ksq >> 4) != home or not heavy[1 - c]:
                    continue
                kf = ksq & 7
                front = home + 1 if c == 0 else home - 1
                cage = True
                for g in (kf - 1, kf, kf + 1):
                    if not 0 <= g < 8:
                        continue
                    if front not in pawn_files[c][g]:
                        cage = False
                        break
                if cage:
                    mg -= sign * CAGE

        # king attack zone (middlegame only): every one of our pieces bearing
        # on the squares around the enemy king is mating pressure, and the
        # same count against us is danger - the classic king-safety term
        if phase > 8:
            for me in (0, 1):
                ek = self.kings[me ^ 1]
                ef, er = ek & 7, ek >> 4
                hits = 0
                for df in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        ff, rr = ef + df, er + dr
                        if 0 <= ff < 8 and 0 <= rr < 8 \
                                and not (df == 0 and dr == 0):
                            hits += len(self.attackers_of(rr * 16 + ff, me))
                if me == 0:
                    mg += 4 * hits
                else:
                    mg -= 4 * hits

        score = (mg * phase + pg * (24 - phase)) // 24
        if self.side:
            score = -score
        score += 6                                    # tempo for side to move

        # endgame driving: bare enemy king -> march ours to the edge and
        # bring our king close, or a flat material score shuffles forever
        for me in (0, 1):
            enemy = me ^ 1
            if nonpawns[me] and not nonpawns[enemy]:
                ef, er = self.kings[enemy] & 7, self.kings[enemy] >> 4
                to_edge = min(ef, 7 - ef) + min(er, 7 - er)
                kf, kr = self.kings[me] & 7, self.kings[me] >> 4
                drive = 8 * (6 - to_edge) + 6 * (7 - max(abs(kf - ef),
                                                          abs(kr - er)))
                score += drive if me == self.side else -drive
                break
        return score

    # ---------------------------------------------------------- ordering

    def order(self, moves, ttmove, ply, pm=0):
        b = self.b
        side = self.side
        killers = self.killers[ply]
        hist = self.hist
        counter = self.counter.get(pm)
        out = []
        for m in moves:
            if m == ttmove:
                out.append((1 << 26, m))
                continue
            frm = m & 255
            to = (m >> 8) & 255
            flags = m >> 19
            promo = (m >> 16) & 7
            victim = b[to]
            if not victim and flags & F_EP:
                victim = P if side == 0 else P + 8
            if victim:
                s = (1 << 20) + 100 * VAL[victim & 7] - VAL[b[frm] & 7]
                if s > (1 << 20) and self.see(m) < -40:
                    # a clearly losing capture is worth less than a good
                    # quiet: search it late, not first
                    s = (1 << 10) + hist[frm][to]
            elif promo:
                s = (1 << 15) + VAL[promo]
            elif m in killers:
                s = 1 << 16
            elif counter is not None and m == counter:
                # the move that refuted the same opponent move before
                s = (1 << 15) - 1 + hist[frm][to]
            else:
                s = hist[frm][to]
            out.append((s, m))
        out.sort(reverse=True, key=lambda pair: pair[0])
        return out

    # -------------------------------------------------------- null move

    def make_null(self):
        undo = (self.ep, self.z, self.side, self.halfm)
        self.halfm = 0
        z = self.z ^ ZO_SIDE
        if self.ep >= 0:
            z ^= _ep_key(self.ep)
        self.ep = -1
        self.z = z
        self._push_rep(z)
        self.side ^= 1
        return undo

    def unmake_null(self, undo):
        self._pop_rep(self.z)
        self.ep, self.z, self.side, self.halfm = undo

    # ------------------------------------------------------- quiescence

    def qsearch(self, alpha, beta, ply):
        self.nodes += 1
        if self.nodes & 1023 == 0 and time.perf_counter() > self.deadline:
            raise TimeoutError
        if self.attacked(self.kings[self.side], self.side ^ 1):
            # being in check is itself tactical: stand pat and capture
            # pruning are nonsense there, so search evasions instead -
            # only in checked nodes, which keeps the hot path untouched
            if ply >= MAXPLY:
                return self.evaluate()
            best = -INF
            for _s, m in self.order(self.gen_legal(), None, 0):
                u = self.make(m)
                sc = -self.qsearch(-beta, -alpha, ply + 1)
                self.unmake(m, u)
                if sc >= beta:
                    return sc
                if sc > best:
                    best = sc
                    if sc > alpha:
                        alpha = sc
            return best
        stand = self.evaluate()
        if ply >= MAXPLY:
            return stand
        if stand >= beta:
            return stand
        if stand > alpha:
            alpha = stand
        # the TT was blind to qsearch so far: reuse any stored line here,
        # only as a fail-high bound (every qsearch score we keep is a
        # achieved line, hence a true lower bound, never an exact claim)
        ent = self.tt.get(self.z)
        ttmove = ent[3] if ent is not None else None
        if ent is not None and ent[0] == 0 and ent[2] == 1 \
                and ent[1] >= beta:
            return ent[1]
        best = alpha
        bestm = ttmove
        moves = self.order(self.gen(caps=True), ttmove, 0)
        for _s, m in moves:
            to = (m >> 8) & 255
            victim = self.b[to]
            if not victim and (m >> 19) & F_EP:
                victim = self.b[to - 16 if self.side == 0 else to + 16]
            if victim and stand + VAL[victim & 7] + 120 < alpha:
                continue                       # delta pruning
            if victim and self.see(m) < -80:
                continue                       # clearly unsound capture
            u = self.make(m)
            if self.attacked(self.kings[self.side ^ 1], self.side):
                self.unmake(m, u)
                continue
            sc = -self.qsearch(-beta, -alpha, ply + 1)
            self.unmake(m, u)
            if sc >= beta:
                old = self.tt.get(self.z)
                if old is None or old[0] <= 0:
                    self.tt[self.z] = (0, sc, 1, m)
                return sc
            if sc > best:
                best = sc
                bestm = m
                if sc > alpha:
                    alpha = sc
        old = self.tt.get(self.z)
        if old is None or old[0] <= 0:
            self.tt[self.z] = (0, best, 1, bestm)
        return best

    # ------------------------------------------------------ main search

    def negamax(self, depth, alpha, beta, ply):
        self.nodes += 1
        if self.nodes & 1023 == 0 and time.perf_counter() > self.deadline:
            raise TimeoutError
        # a position seen twice already on this path repeats a third time
        # next: a bare draw score makes a winning engine shuffle when the
        # line costs nothing, so grade it by the static eval - dead-drawn
        # positions still count a repeat, clearly won ones steer around it
        if ply and self.reps.get(self.z, 0) >= 2:
            return -40 if self.evaluate() >= 80 else 0
        alpha0 = alpha
        if ply:
            ent = self.tt.get(self.z)
        else:
            ent = None
        ttmove = None
        if ent is not None and ent[0] >= depth:
            s, flag, ttmove = ent[1], ent[2], ent[3]
            if s >= MATE_THRESHOLD:
                s -= ply
            elif s <= -MATE_THRESHOLD:
                s += ply
            if flag == 0:
                return s
            if flag == 1:
                if s >= beta:
                    return s
                if s > alpha:
                    alpha = s
            else:
                if s <= alpha:
                    return s
                if s < beta:
                    beta = s
        if ply >= MAXPLY:
            return self.evaluate()
        in_check = self.attacked(self.kings[self.side], self.side ^ 1)
        if in_check and depth < 10:
            depth += 1                         # check extension
        if depth <= 0:
            return self.qsearch(alpha, beta, ply)
        if not in_check:
            # mate-distance pruning: the side to move cannot be mated on its
            # own turn, so no genuine score exists outside the mate window at
            # this ply - tighten the bounds and cut when they cross
            ma = -MATE + ply
            mb = MATE - ply - 1
            if alpha < ma:
                alpha = ma
            if beta > mb:
                beta = mb
            if alpha >= beta:
                return alpha
        moves = self.gen_legal()
        if not moves:
            return -MATE + ply if in_check else 0
        ordered = self.order(moves, ttmove, ply, self.pv[ply])
        # probcut: at deep nodes, if the static eval already clears
        # beta + margin, a shallow wide search over forcing moves proving
        # it lets us cut without expanding the whole node
        if depth >= 5 and not in_check and beta < MATE_THRESHOLD \
                and self.evaluate() >= beta + 120:
            rc = beta + 120
            for _s, m in ordered:
                to = (m >> 8) & 255
                if not self.b[to] and not (m >> 16) & 7 \
                        and not (m >> 19) & F_EP:
                    continue                 # captures and promotions only
                u = self.make(m)
                if self.attacked(self.kings[self.side ^ 1], self.side):
                    self.unmake(m, u)
                    continue
                sc = -self.negamax(depth - 4, -INF, -rc, ply + 1)
                self.unmake(m, u)
                if sc >= rc:
                    return sc
        if (not in_check and depth >= 3 and self.npm[self.side] >= 3
                and abs(beta) < MATE_THRESHOLD):
            u = self.make_null()
            r = -self.negamax(depth - 3, -beta, -beta + 1, ply + 1)
            self.unmake_null(u)
            if r >= beta and r < MATE_THRESHOLD:
                return beta
        best = -INF
        bestm = None
        # horizon futility: static eval far under alpha means quiet moves
        # cannot raise it, so they are skipped outright (never in check).
        # depth-2 futility was tried: the extra static eval at every
        # depth-2 node costs more than the cutoffs save, so keep it at 1 -
        # and even that only computed once a quiet candidate actually needs
        # it (feval stays None until then).
        feval = None
        fm = 150
        lmp = 3 + depth * depth
        for i, (_s, m) in enumerate(ordered):
            to = (m >> 8) & 255
            quiet = (not self.b[to] and not (m >> 16) & 7
                     and not (m >> 19) & F_EP)
            if best > -INF and not in_check and quiet:
                if depth <= 3 and i >= lmp:            # late-move pruning
                    continue
                if depth == 1:
                    if feval is None:
                        feval = self.evaluate()
                    if feval + fm < alpha:
                        continue                       # futility
            u = self.make(m)
            self.pv[ply + 1] = m
            if i >= 4 and depth >= 3 and quiet and not in_check:
                # the deeper into the ordering a quiet move sits, the less it
                # deserves a full look; the re-search below still catches any
                # reduction that was too bold
                red = 3 if i >= 10 else 2
                sc = -self.negamax(depth - red, -beta, -alpha, ply + 1)
                if sc > alpha:            # reduction may have been too bold
                    sc = -self.negamax(depth - 1, -beta, -alpha, ply + 1)
            else:
                sc = -self.negamax(depth - 1, -beta, -alpha, ply + 1)
            self.unmake(m, u)
            if sc > best:
                best = sc
                bestm = m
                if sc > alpha:
                    alpha = sc
            if alpha >= beta:
                if quiet:
                    k = self.killers[ply]
                    if m != k[0] and m not in k:      # 3-slot killers
                        self.killers[ply] = ([m] + k)[:3]
                    self.counter[self.pv[ply]] = m   # refutation of the last move
                    # aging history: decay the old value toward the new
                    # strike instead of letting early-deep bonuses pile up
                    frm = m & 255
                    to = (m >> 8) & 255
                    self.hist[frm][to] = (self.hist[frm][to]
                                          + depth * depth) >> 1
                break
        store = best
        if best >= MATE_THRESHOLD:
            store = best + ply
            flag = 1 if best >= beta else 0
        elif best <= -MATE_THRESHOLD:
            store = best - ply
            flag = 2 if best <= alpha0 else 1
        elif best > alpha0:
            store = best
            flag = 1 if best >= beta else 0
        else:
            store = best
            flag = 2
        # replacement: overwrite unless the stored entry is strictly deeper
        old = self.tt.get(self.z)
        if old is None or old[0] <= depth:
            self.tt[self.z] = (depth, store, flag, bestm)
        return best

    # -------------------------------------------------------------- root

    def search(self, budget, maxdepth):
        self.nodes = 0
        self.last_depth = 0
        self.second = None
        start = time.perf_counter()
        self.deadline = start + budget
        ext_left = 2               # unstable-BB extensions allowed this move
        legal = self.gen_legal()
        if not legal:
            return None, -INF
        if len(legal) == 1:
            return legal[0], 0
        best = None
        prev = None
        best_score = -INF
        # a mid-iteration timeout unwinds through root_pass with moves
        # still applied, so the board would be several plies deep when we
        # return; snapshot the root position and restore it on the way out
        snap = self.snapshot()
        try:
            self.root_scores = {}
            for depth in range(1, maxdepth + 1):
                if self.root_scores:
                    # order the new iteration by how the moves scored in
                    # the last completed one: the refutation/best order is
                    # already known, so pruning bites from move number one
                    rs = self.root_scores
                    root_moves = [(rs.get(m, -INF), m) for m in legal]
                    root_moves.sort(reverse=True)
                else:
                    root_moves = self.order(legal, prev, 0)
                if (depth >= 6 and best_score > -INF
                        and abs(best_score) < MATE_THRESHOLD):
                    # aspiration pays off only at real depth: at shallow
                    # depths a mispredict wastes a whole re-search and the
                    # tiny budget cannot spare it. the window is widened
                    # graduated rather than once and for all, because a
                    # single +80 step kept landing inside the same failing
                    # window and threw the depth away on a re-search
                    lo, hi = best_score - 30, best_score + 30
                    for attempt in range(4):
                        chosen, score = self.root_pass(root_moves, depth,
                                                       lo, hi)
                        if chosen is None or score <= lo:
                            lo = -INF if attempt == 3 else lo - 100 * (attempt + 1)
                        elif score >= hi:
                            hi = INF if attempt == 3 else hi + 100 * (attempt + 1)
                        else:
                            break
                    else:
                        chosen, score = self.root_pass(root_moves, depth,
                                                       -INF, INF)
                else:
                    chosen, score = self.root_pass(root_moves, depth,
                                                   -INF, INF)
                if chosen is None:
                    break                      # aspiration never confirmed
                # unstable bound: this iteration flipped the best move, so
                # the old "best" was a cutoff mirage - double the budget
                # (hard cap 20s) up to twice to settle the verdict
                if (best is not None and chosen != best
                        and abs(score) < MATE_THRESHOLD
                        and ext_left and self.deadline < start + 2 * budget):
                    ext_left -= 1
                    self.deadline = min(start + 2 * budget, start + 20.0)
                best = prev = chosen
                best_score = score             # only a completed depth counts
                self.last_depth = depth
                if score >= MATE_THRESHOLD:
                    break
                # the clock may have run during this round; it finished, so
                # its verdict is the strongest thing we have and is kept -
                # only the next round is skipped
                if time.perf_counter() > self.deadline:
                    break
                if len(self.tt) > 300_000:
                    self.tt.clear()
        except TimeoutError:
            self.restore(snap)
        return best, best_score

    def root_pass(self, root_moves, depth, alpha, beta):
        """One root iteration inside [alpha, beta]; returns (move, score)."""
        score = alpha
        chosen = None
        second = None
        scores = {}
        for i, (_s, m) in enumerate(root_moves):
            u = self.make(m)
            sc = -self.negamax(depth - 1, -beta, -max(score, alpha), 1)
            self.unmake(m, u)
            scores[m] = sc
            if sc > score:
                if chosen is not None:
                    second = chosen
                score = sc
                chosen = m
        self.second = second
        self.root_scores = scores
        return chosen, score


class FligBot(Bot):
    name = "fligbot classic"
    author = "fligchess"
    version = 5                 # v5: modes narrowed to classic + minefield
    CONFIG = {}                 # no per-seat options: it always plays to win
    MODES = ("2p/*/classic", "2p/*/minefield")   # the plain two-army board

    budget = 3.0                # seconds per move; the app's --budget overrides
    MAX_SEARCH_DEPTH = 48       # iterative deepening is really cut off by time

    def __init__(self):
        super().__init__()
        self._engine = Engine()

    def configure(self, options):
        super().configure(options)

    # ------------------------------------------------------------- search

    def choose(self, position):
        """Its single strongest move: an opening-book line when one matches,
        otherwise iterative deepening within `self.budget` seconds, followed
        by the two horizon audits - the loose-material `refine` and the
        moved-piece `blunder_pass`. Neither invents a plan; they only refuse
        a pick the shallow search walked into, so tactics always win."""
        moves = position.legal_moves()
        if not moves:
            return None
        # the book only keys on standard mainline FENs, so it is inert for
        # the imperfect-info variants (fog/minefield) - those hit the search
        book = lookup_book(position.fen())
        if book:
            mv = pick_book_move(book, moves)
            if mv is not None:
                return mv

        eng = self._engine
        eng.load(position.fen())
        legal = eng.gen_legal()
        m, score = eng.search(self.budget, self.MAX_SEARCH_DEPTH)
        if m is None:
            return random.choice(moves)
        # horizon-blind safety audit: the filter only reshuffles between
        # moves the search already rated near-equal, so tactics the engine
        # genuinely found always win
        m = eng.refine(legal, eng.root_scores, m, score) or m
        m = eng.blunder_pass(legal, eng.root_scores, m, score) or m
        frm = m & 255
        to = (m >> 8) & 255
        promo = {5: "q", 4: "r", 3: "b", 2: "n"}.get((m >> 16) & 7)
        mv = Move((frm & 15, frm >> 4), (to & 15, to >> 4), promo)
        return mv if mv in moves else random.choice(moves)


if __name__ == "__main__":
    # tiny self-test: python bots/fligbot_classic.py
    eng = Engine()
    for label, fen, counts in (
            ("startpos", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
             (20, 400, 8902, 197281)),
            ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
             (48, 2039, 97862))):
        eng.load(fen)
        got = [eng.perft(d) for d in range(1, len(counts) + 1)]
        print(label, "perft", got, "expected", list(counts),
              "OK" if got == list(counts) else "MISMATCH")
    bot = FligBot()
    bot.configure({})
    pos = Position(fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    print(f"{bot.name} picks:", bot.choose(pos))
    # the moved-piece audit: a queen marched onto a square the d5 pawn
    # attacks, in an otherwise empty board, must be talked off it
    eng.load("3k4/8/8/3p4/8/8/8/Q3K3 w - - 0 1")
    legal = eng.gen_legal()
    pull = pack(0, 52)                          # Qa1-e4, straight onto a pawn
    keep = eng.blunder_pass(legal, {m: 0 for m in legal}, pull, 0)
    print("blunder guard keeps the queen off e4:", keep != pull)
    # the opening book: every advertised line must be legal end-to-end, so a
    # mistyped token surfaces here instead of silently truncating its line
    truncated = []
    for line in _BOOK_LINES:
        p = Position(fen=_BOOK_START)
        for tok in line:
            mv = _book_move(tok)
            if mv not in p.legal_moves():
                truncated.append(" ".join(line) + " @ " + tok)
                break
            p = p.make(mv)
    print(f"opening book: {len(_BOOK_LINES)} lines,",
          "all legal" if not truncated else f"TRUNCATED {truncated}")
