"""fligbot classic 2p - the bundled FligChess bot for the two-army board.

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

VAL = (0, 100, 340, 370, 540, 1050, 20000)     # per piece id (P..K)
NV = (0, 0, 3, 3, 5, 9, 0)                    # phase weight per piece id

# how hard the engine argues with a line that goes nowhere. A two-player game
# draws on the third arrival at a position, so `seen` of 2 means the shuffle is
# one move from ending the game - which matters only to the side that is
# better, and that is exactly the engine a flat draw score tempts.
PROGRESS_WARN = 2         # seen count at which a repeat stops being free
PROGRESS_SLACK = 120      # extra centipawns a forward move may cost per count

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
# rook square -> the right that rook's departure (or capture) loses. Bits 1/2
# are white's K/Q and 4/8 black's k/q, and in 0x88 a8 is 112 while h8 is 119:
# the black pair used to be written {112: 4, 120: 8}, which both charged a8's
# departure to black's KINGSIDE right and named a square that is off the board,
# so the bot forfeited castles it still had and kept one it had lost.
CORNER_RIGHT = {7: 1, 0: 2, 119: 4, 112: 8}

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
        0, 0, 5, 5, 5, 5, 0, 0,
        -10, 5, 5, 5, 5, 5, 5, -10,
        -10, 0, 5, 0, 0, 5, 0, -10,
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
    P: (0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
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
        0, 0, 0, 0, 0, 0, 0, 0),
    Q: (-20, -10, -10, -5, -5, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 5, 5, 5, 0, -10,
        -5, 0, 5, 5, 5, 5, 0, -5,
        0, 0, 5, 5, 5, 5, 0, 0,
        -10, 5, 5, 5, 5, 5, 5, -10,
        -10, 0, 5, 0, 0, 5, 0, -10,
        -20, -10, -10, -5, -5, -10, -10, -20),
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

# -------------------------------------------- derived material / neighbour tables
# The value of every piece on every square with its own colour's sign folded
# in. `Engine` carries the running sum of these and touches only the two or
# three squares a move changes, so a search node no longer re-reads VAL and
# the piece-square tables for all thirty pieces on the board - which is what
# the profile said the time was going on. They are rebuilt on the first load()
# after anything rebinds VAL (the lab's runtime tuner does exactly that), so a
# parameter set applied in memory scores exactly like a baked file.
def _pval_tables():
    pmg = [0] * (15 * 128)
    ppg = [0] * (15 * 128)
    for p in (1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14):   # the real piece ids
        t = p & 7
        white = p < 8
        base = p * 128
        for sq in SQ64:
            f, r = sq & 7, sq >> 4
            idx = (7 - r) * 8 + f if white else r * 8 + (7 - f)
            v = VAL[t] + PST_MG[t][idx]
            w = VAL[t] + PST_PG[t][idx]
            pmg[base + sq] = v if white else -v
            ppg[base + sq] = w if white else -w
    return pmg, ppg


_PV_SRC = VAL
_PMG, _PPG = _pval_tables()          # built against the file's own VAL


def _pv():
    global _PV_SRC, _PMG, _PPG
    if _PV_SRC is not VAL:
        _PMG, _PPG = _pval_tables()
        _PV_SRC = VAL
    return _PMG, _PPG


# the same handful of squares asked over and over by the structural terms:
# adjacent files for pawn support/passed tests, and the ring of squares around
# a king for the attack zone. The three-file shape the passed test needs is
# just this plus the file itself, and it is tabulated per colour below
ADJ = [tuple(g for g in (f - 1, f + 1) if 0 <= g < 8) for f in range(8)]
# the pawn terms ask nothing but "does this file hold a rank north of r", and
# a file's ranks fit in one byte: BELOW[r] carries a bit for every rank under r
# and ABOVE[r] for every rank at or above it, so each of those walks becomes a
# single AND. The two tables are complements, which is why ABOVE is derived
BELOW = [(1 << r) - 1 for r in range(9)]
ABOVE = [~v for v in BELOW]
KING_NB = [None] * 128
for _s in SQ64:
    _nb = []
    _f, _r = _s & 7, _s >> 4
    for _df in (-1, 0, 1):
        for _dr in (-1, 0, 1):
            if _df or _dr:
                _ff, _rr = _f + _df, _r + _dr
                if 0 <= _ff < 8 and 0 <= _rr < 8:
                    _nb.append(_rr * 16 + _ff)
    KING_NB[_s] = tuple(_nb)
KING_RING = [frozenset(v) if v is not None else frozenset() for v in KING_NB]

# which piece types actually attack along a given ray, so the check/pin sweep
# can ask "is this the kind of slider that hits down this delta" in one lookup
_RAY_KIND = {d: (B, Q) for d in DIAG}
_RAY_KIND.update({d: (R, Q) for d in STRAIGHT})

# the file and rank step of every ray delta: in 0x88 a delta of 1 is one file
# sideways, 16 is one rank forwards, and 15/17 are the two diagonal steps
_RAY_STEP = {}
for _d in DIAG + STRAIGHT:
    if abs(_d) == 1:                      # a rank sweep: the file moves
        _RAY_STEP[_d] = (_d, 0)
    elif abs(_d) == 16:                   # a file sweep: the rank moves
        _RAY_STEP[_d] = (0, 1 if _d > 0 else -1)
    else:                                 # a diagonal moves both at once
        _RAY_STEP[_d] = (1 if _d in (17, -15) else -1,
                         1 if _d in (17, 15) else -1)


def _reaches(step, gap):
    """Can a ray whose axis step is `step` (-1, 0 or 1) land on the 3x3 box
    that sits `gap` squares away along that axis? A ray that does not change
    the coordinate covers one line of it, so the box has to straddle that
    line; a ray that does sweeps it in one direction only, so the box has to
    lie that way and be at least one step ahead."""
    if step == 0:
        return -1 <= gap <= 1
    return gap >= 0 if step > 0 else gap <= 0


def _ray_table(deltas):
    """For every (file gap, rank gap) between a slider and the enemy king, the
    deltas of `deltas` whose ray can still reach the king's ring. Indexed
    (gap + 7) * 15 + (gap + 7), so the search reads it with one multiply.

    Built over the full box the ring could occupy, which at the edge of the
    board is smaller than three by three: that keeps the table generous - it
    can offer a ray that steps off the board or past a truncated ring, and it
    never drops one that would have scored, which is what makes using it free
    of any change to the score itself."""
    tab = []
    for af in range(-7, 8):
        for ar in range(-7, 8):
            tab.append(tuple(d for d in deltas
                            if _reaches(_RAY_STEP[d][0], af)
                            and _reaches(_RAY_STEP[d][1], ar)))
    return tab


_RAY_STRAIGHT = _ray_table(STRAIGHT)
_RAY_DIAG = _ray_table(DIAG)
_RAY_ALL = _ray_table(KING_DELTAS)


def _zone(b, pcs, ksq, by):
    """How many attacks the pieces of colour `by` make on `ring`, the ring of
    squares around the enemy king. The attack relation is symmetric, so
    summing over the pieces gives the same total as the classic count from
    each ring square outward - and it only walks the sliders that are out
    there, instead of eight rays from all eight neighbours, which was the
    single most expensive term in the whole evaluation.

    Even that walked too much: a rook on the board somewhere always has some
    ray, and eight directions of walking to the board edge is what most
    positions pay for a term that comes out zero. A ray can only reach the
    king's 3x3 box if it runs along an axis the box sits on and in the
    direction that box lies, both of which follow from the two gaps between
    the piece and the king - so which of a piece's rays deserve a walk at all
    is looked up in a table over those gaps (_ray_table below) instead of
    discovered by walking. Fewer rays walked, same count: a ray the table
    drops could not have stepped on a ring square.
    """
    ring = KING_RING[ksq]
    if not ring:
        return 0
    o = 8 * by
    kf, kr = ksq & 7, ksq >> 4
    n = 0
    # a white pawn takes upwards, so the two squares it threatens are one rank
    # HIGHER and a file either side - the reverse of the deltas used when
    # scanning for an attacker standing below a given square
    for sq in pcs[1 + o]:
        for d in ((15, 17) if by == 0 else (-15, -17)):
            if sq + d in ring:
                n += 1
    for sq in pcs[2 + o]:
        for d in N_DELTAS:
            if sq + d in ring:
                n += 1
    for sq in pcs[6 + o]:
        for d in KING_DELTAS:
            if sq + d in ring:
                n += 1
    for tid, tab in ((3 + o, _RAY_DIAG), (4 + o, _RAY_STRAIGHT),
                     (5 + o, _RAY_ALL)):
        for sq in pcs[tid]:
            # the gaps are measured piece -> king, which is the direction a
            # ray has to travel to get there
            key = (kf - (sq & 7) + 7) * 15 + (kr - (sq >> 4) + 7)
            for d in tab[key]:
                t = sq + d
                while not t & 0x88:
                    if t in ring:
                        n += 1
                    if b[t]:
                        break
                    t += d
    return n


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
MOBW = {N: 0}
# MOBW is baked over by the tuner, so which types are actually scored is only
# known once the numbers are in: work that out here instead of letting every
# node iterate the dict and throw every entry straight away
MOBILE_ON = {tid: spec for tid, spec in MOBILE.items() if MOBW.get(tid, 0)}

# evaluation terms the piece-square tables cannot express: a piece standing
# where an enemy pawn can chase it, a slider with its exits being closed, a
# king bolted into its own pawn cage and a pawn backed by its neighbour
HARASS = 0
TRAPPED = 40
CAGE = 15
SUPPORT = 12

# ---------------------------------------------------------------- tuning knobs
# Every remaining weight the evaluation scores with, named. A hand-guessed
# number is a guess: the top engines fit each of these separately to a large
# corpus of played-out games (Texel's method, now standard) and keep only the
# ones a match says are better. Naming them costs nothing at run time - the
# lookups below read them once per term - and it is what lets the tuner price
# an outpost against a doubled pawn instead of leaving one of them arbitrary.
# Each PAIR is the same feature priced twice, middlegame and endgame, which is
# what the phase taper interpolates between. One constant per line: the lab
# bakes tuned values back into this file by name, so they have to be legible
# to a line-oriented edit.
OUTPOST_MG = 21                 # knight on a protected, pawn-proof 5th/6th
OUTPOST_PG = 20
BISHOPPAIR_MG = 60              # the two bishops command both colours
BISHOPPAIR_PG = 40
BADBISH_MG = 13                  # own pawns fixed on the bishop's colour
BADBISH_PG = 20
DOUBLED_MG = 6                 # a pawn behind another pawn cannot move
DOUBLED_PG = 20
ISOLATED_MG = 18                # no neighbour pawn on the adjacent files
ISOLATED_PG = 20
BACKWARD_MG = 0                # every neighbour stands ahead of it
BACKWARD_PG = 6
ESCORT_MG = 3                   # passer with a pawn directly behind it
ESCORT_PG = 8
PHALANX_MG = 9                  # passer with a neighbour level with it
PHALANX_PG = 25
OPENFILE_MG = 24                # heavy piece on a file with no pawns at all
OPENFILE_PG = 22
SEMIFILE_MG = 12                 # ... none of ours, so a push is coming
SEMIFILE_PG = 17
SEVENTH_MG = 34                 # heavy piece on the 7th, one rank from mate
SHIELD1_MG = 6                 # no pawn directly in front of the king
SHIELD2_MG = 0                  # ... and none two ranks up either
ZONE_MG = 9                     # piece attacks bearing on the enemy king
TEMPO_MG = 15                    # the side to move is a move ahead
DRIVE_EDGE_MG = 0               # endgame: march the bare king to the edge
DRIVE_NEAR_MG = 3               # ... and bring our king close to it

# ------------------------------------------------------- search pruning knobs
# How far the search is allowed to trust the evaluation. The eval terms above
# decide what a position is worth; these decide how often the search gets to
# ask, which at a few thousand nodes a second is the other half of the strength.
# Each one is a bet that the static score is not that wrong at that depth, and
# a bet the lab settles with games rather than with confidence.
RFP_DEPTH = 3                   # reverse futility: shallow nodes only
RFP_MARGIN = 85                 # times the depth: spare eval needed to cut


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


def _ray_clear(b, to, step, ksq):
    """Nothing between `to` and `ksq` along `step` (neither end counted)."""
    a = to + step
    while a != ksq:
        if a & 0x88 or b[a]:
            return False
        a += step
    return True


def _aims(b, t, to, ksq, own):
    """Does a piece of type `t` of the colour `own` (0 or 8), sitting on `to`,
    attack the king square `ksq`?

    The sliders walk their whole ray, so a piece standing between them blocks
    the claim - which is the whole reason this is not a shape lookup. `to` is
    treated as carrying the piece, as it will once the move is made."""
    df = (ksq & 7) - (to & 7)
    dr = (ksq >> 4) - (to >> 4)
    if t == P:
        return abs(df) == 1 and dr == (1 if own == 0 else -1)
    if t == N:
        return sorted((abs(df), abs(dr))) == [1, 2]
    if t == K:
        return max(abs(df), abs(dr)) == 1
    step = ((0 if df == 0 else 1 if df > 0 else -1)
            + (0 if dr == 0 else 16 if dr > 0 else -16))
    if t in (B, Q) and abs(df) == abs(dr) != 0:
        return _ray_clear(b, to, step, ksq)
    if t in (R, Q) and (df == 0) != (dr == 0):
        return _ray_clear(b, to, step, ksq)
    return False


def _uncovers(b, frm, ksq, own):
    """Does emptying `frm` open a check from one of our own sliders?

    The piece that is leaving must be the only thing between the enemy king
    and a friendly slider on the same line: walk out from `frm` towards the
    king and require an unobstructed path, then walk the other way and see
    whether the first piece there slides along that line."""
    for d in KING_DELTAS:
        slides = (B, Q) if d in DIAG else (R, Q)
        a = frm + d
        while not a & 0x88 and a != ksq:
            if b[a]:
                break
            a += d
        if a != ksq:
            continue                      # the king is not down this ray
        s = frm - d
        while not s & 0x88:
            p = b[s]
            if p:
                return (p & 8) == own and (p & 7) in slides
            s -= d
    return False


def _may_check(b, m, ksq, own):
    """Could this move give check? Asked only of a move the horizon prunes
    are about to drop.

    Late-move pruning keeps just `3 + depth*depth` quiet moves at shallow
    nodes and depth-1 futility throws every quiet one whose static score
    cannot reach alpha - and a quiet mate is made of exactly such a move. The
    puzzle row `8/8/6b1/8/8/8/1K1R3N/7k w` mates with 1.Nf3 Bh7 2.Rh2#: the
    rook walk is quiet, sits at index 16 of 25 in its node, and was pruned at
    every depth under 8, so the engine scored the position on the way to a
    draw instead of playing the mate.

    The test is geometric and exact rather than a lookup, because the first
    version - a blocker-blind shape check - cost twice the nodes at depth 8
    (85,321 against 34,979) and a whole ply at a one-second budget, which is
    more than the mate is worth. This one puts the startpos depth 8 at 62,183
    nodes, about five sevenths more than the 34,979 the unguarded pruning
    needed, and it finds the puzzle mate at depth 3 for 233 nodes. The lab
    league still came out 9W-2L-1D (+117 Elo) against the shipped bot, so the
    tax pays for itself."""
    if (m >> 19) & F_CASTLE:
        return True            # the rook hops too; a rare check, never pruned
    t = b[m & 255] & 7
    to = (m >> 8) & 255
    return _aims(b, t, to, ksq, own) or _uncovers(b, m & 255, ksq, own)


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
        self._mg = 0                 # summed material + mg table, white minus
        self._pg = 0                 # ... and the endgame half of the taper
        self._pmg = _PMG             # the tables those two are kept against
        self._ppg = _PPG
        self.tt = {}
        self._ecache = {}              # zobrist -> static score, a pure memo
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
        self._reset_acc()
        z = 0
        for sq in SQ64:
            p = b[sq]
            if p:
                z ^= ZO_PIECE[p][sq]
        z ^= CASTLE_KEY[self.castle] ^ _ep_key(self.ep)
        if self.side:
            z ^= ZO_SIDE
        self.z = z
        self.reps = {z: 1}

    def _reset_acc(self):
        """Rebuild the incremental eval state from the board: the summed
        material plus table score and the phase weights. Only load() and an
        abandoned search call it - a move updates the score in place."""
        b = self.b
        self._pmg, self._ppg = _pv()      # make/unmake read these, not _pv()
        pmg, ppg = self._pmg, self._ppg
        npm = [0, 0]
        mg = pg = 0
        for sq in SQ64:
            p = b[sq]
            if p:
                k = p * 128 + sq
                mg += pmg[k]
                pg += ppg[k]
                t = p & 7
                if t not in (P, K):
                    npm[0 if p < 8 else 1] += NV[t]
        self._mg = mg
        self._pg = pg
        self.npm = npm

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
                self.halfm, self.kings[me], self.npm[me], self.npm[enemy],
                self._mg, self._pg)

        b[frm] = 0
        if vsq >= 0:
            b[vsq] = 0
        moved = piece if not promo else promo | (piece & 8)
        b[to] = moved

        # the running eval follows the board: a move changes the material and
        # table score only through the squares it touches, so those are
        # adjusted here instead of the whole board being re-added up at the
        # next node. nothing else is incremental - make/unmake runs for every
        # pseudo-legal move, an eval once per node, and the extra bookkeeping
        # would cost more than the scan it saves
        pmg, ppg = self._pmg, self._ppg
        mg = self._mg - pmg[piece * 128 + frm] + pmg[moved * 128 + to]
        pg = self._pg - ppg[piece * 128 + frm] + ppg[moved * 128 + to]
        if cap:
            csq = vsq if vsq >= 0 else to
            mg -= pmg[cap * 128 + csq]
            pg -= ppg[cap * 128 + csq]

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
                rk = rook * 128
                mg += pmg[rk + rt] - pmg[rk + rf]
                pg += ppg[rk + rt] - ppg[rk + rf]

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
        self._mg = mg
        self._pg = pg
        self._push_rep(z)
        self.side = enemy
        return undo

    def unmake(self, m, undo):
        frm = m & 255
        to = (m >> 8) & 255
        flags = m >> 19
        (piece, cap, vsq, z, castle, ep, halfm, king_sq, npm_me, npm_en,
         mg, pg) = undo
        b = self.b
        me = self.side ^ 1
        self._pop_rep(self.z)
        b[frm] = piece
        if vsq >= 0:
            b[vsq] = cap
            b[to] = 0
        else:
            b[to] = cap
        # the pre-move totals rode along in `undo`, so they are put back
        self._mg = mg
        self._pg = pg
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
        self._reset_acc()          # the board came back; rebuild what it means

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

    # ---------------------------------------------------- check and pin state
    # "does this move leave my king in check?" used to be answered the slow
    # way, once for every pseudo-legal move: make it, sweep for attacks on the
    # king, take it back - forty sweeps per node, which the profile showed to
    # be most of the search. The answer is nearly always decided before the
    # move is played at all, so the node sweeps its own king once and the only
    # moves still needing the make/unmake test are the ones that can genuinely
    # uncover: a pinned piece, an en-passant capture (which empties two squares
    # on a rank at once) and castling (which moves the rook too).

    def _king_state(self):
        """(checkers, pinned) for the side to move.

        `checkers` is a list of (square, ray delta); the delta is 0 for a
        knight, a pawn or the enemy king, none of which can be blocked - they
        can only be taken. `pinned` holds the squares of our own pieces that
        are the single shield on an enemy slider's ray to our king."""
        b = self.b
        me = self.side
        ks = self.kings[me]
        ab = me ^ 1                      # attacker colour: everything below is
        o = 8 * ab                       # read from the king, not from us
        checkers = []
        pinned = ()
        for d in KING_DELTAS:
            t = ks + d
            own = 0                        # first of ours on this ray, if any
            while not t & 0x88:
                p = b[t]
                if p:
                    # the first piece on the ray decides everything. an enemy
                    # one checks us if it slides along d - and if one of ours
                    # stood between, that one is pinned: it is the only thing
                    # in the way. two of ours in a row pins neither.
                    if (p & 8) == o:
                        if (p & 7) in _RAY_KIND[d]:
                            if own:
                                pinned += (own,)
                            else:
                                checkers.append((t, d))
                        break
                    if own:
                        break
                    own = t
                t += d
        # pawns are the only asymmetric attacker: a white one strikes from two
        # ranks down, so the squares it hits depend on WHO attacks the king
        for d in ((-15, -17) if ab == 0 else (15, 17)):
            t = ks + d
            if not t & 0x88 and b[t] == 1 + o:
                checkers.append((t, 0))
        for d in N_DELTAS:
            t = ks + d
            if not t & 0x88 and b[t] == 2 + o:
                checkers.append((t, 0))
        for d in KING_DELTAS:
            t = ks + d
            if not t & 0x88 and b[t] == 6 + o:
                checkers.append((t, 0))
        return checkers, pinned

    def gen_legal(self, caps=False):
        b = self.b
        me = self.side
        ks = self.kings[me]
        enemy = me ^ 1
        checkers, pinned = self._king_state()
        dbl = len(checkers) > 1
        mask = None
        if checkers and not dbl:
            cs, d = checkers[0]
            mask = {cs}                       # take the checker
            if d:                            # ...or stop a slider behind it
                t = ks + d
                while t != cs:
                    mask.add(t)
                    t += d
        out = []
        for m in self.gen(caps):
            frm = m & 255
            if frm == ks:
                if (m >> 19) & F_CASTLE:
                    u = self.make(m)          # judged with the rook landed
                    if not self.attacked(self.kings[me], enemy):
                        out.append(m)
                    self.unmake(m, u)
                else:
                    # the king walking off its square can step into a ray that
                    # its own body was blocking, so the square is emptied first
                    p = b[ks]
                    b[ks] = 0
                    bad = self.attacked((m >> 8) & 255, enemy)
                    b[ks] = p
                    if not bad:
                        out.append(m)
                continue
            if dbl:
                continue
            if frm in pinned or (m >> 19) & F_EP:
                u = self.make(m)
                if not self.attacked(self.kings[me], enemy):
                    out.append(m)
                self.unmake(m, u)
                continue
            if mask is not None and ((m >> 8) & 255) not in mask:
                continue
            out.append(m)
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
        # the score of a position is a function of the position alone - every
        # term below reads the board, the two kings and the side to move, all
        # of which the zobrist key already commits - so one rich scan per key
        # serves every later visit to it. qsearch asks the same question twice
        # over a line and iterative deepening asks it again at the next depth;
        # this turns that work into a dict lookup, which is what buys the depth
        v = self._ecache.get(self.z)
        if v is not None:
            return v
        b = self.b
        mg = self._mg                 # material plus the mg tables, carried
        pg = self._pg                 # ... by make/unmake, never re-summed
        # one pass files the pieces by type, and every structural term below
        # then walks only the pieces it can say something about - instead of
        # re-deriving the same facts with a type test on all sixty-four squares
        pc = [[] for _ in range(15)]
        for sq in SQ64:
            p = b[sq]
            if p:
                pc[p].append(sq)
        # the taper weight is the phase material of the pieces that are ON the
        # board, which `npm` is not: it is stepped down whenever a piece
        # shuffles, so the counts come from the piece lists instead
        n0, n1 = len(pc[N]), len(pc[N + 8])
        b0, b1 = len(pc[B]), len(pc[B + 8])
        r0, r1 = len(pc[R]), len(pc[R + 8])
        q0, q1 = len(pc[Q]), len(pc[Q + 8])
        heavy = [r0 + q0, r1 + q1]
        nonpawns = [n0 + b0 + heavy[0], n1 + b1 + heavy[1]]
        phase = (NV[N] * (n0 + n1) + NV[B] * (b0 + b1)
                 + NV[R] * (r0 + r1) + NV[Q] * (q0 + q1))
        pawn_files = ([[] for _ in range(8)], [[] for _ in range(8)])
        pmask = ([0] * 8, [0] * 8)     # the same ranks again, filed as bits
        seventh = [0, 0]
        rook_file = [[], []]
        pw_color = [[0, 0], [0, 0]]     # own pawns on each square colour
        bish_color = [[], []]           # colour parity of each side's bishops
        # what a node has to work out from scratch is the structure, and the
        # pieces are already filed by type: each term below walks only the
        # pieces it can say something about instead of all sixty-four squares
        for c in (0, 1):
            for sq in pc[P + 8 * c]:
                f, r = sq & 7, sq >> 4
                pawn_files[c][f].append(r)
                pmask[c][f] |= 1 << r
                pw_color[c][(f + r) & 1] += 1
            for sq in pc[B + 8 * c]:
                bish_color[c].append(((sq & 7) + (sq >> 4)) & 1)
            for sq in pc[R + 8 * c]:
                f, r = sq & 7, sq >> 4
                rook_file[c].append(f)
                if r == (6 if c == 0 else 1):
                    seventh[c] += 1
            for sq in pc[Q + 8 * c]:
                f, r = sq & 7, sq >> 4
                rook_file[c].append(f)
                if r == (6 if c == 0 else 1):
                    seventh[c] += 1
        # one OR per file, kept for the whole scan to reuse: pmnb[c][f] is the
        # ranks colour c holds on the files beside f, and pm3[c][f] adds f's
        # own file. The pawn and outpost terms both ask about exactly these
        # two shapes, and asking once costs one pass instead of one per term
        pmnb = [[0] * 8, [0] * 8]
        pm3 = [[0] * 8, [0] * 8]
        for c in (0, 1):
            m = pmask[c]
            nb, t3 = pmnb[c], pm3[c]
            for f in range(8):
                mf = m[f]
                nbv = 0
                for g in ADJ[f]:
                    nbv |= m[g]
                nb[f] = nbv
                t3[f] = nbv | mf
        # mobility: only the pieces MOBILE names are scored - ray-based
        # mobility costs more nodes/second than it buys, in pure python
        for tid, spec in MOBILE_ON.items():
            wgt = MOBW[tid]
            dirs, ray = spec
            for c in (0, 1):
                sgn = 1 if c == 0 else -1
                for sq in pc[tid + 8 * c]:
                    val = wgt * _mobility(b, sq, 8 * c, dirs, ray)
                    mg += sgn * val
                    pg += sgn * (val // 2)
        for c in (0, 1):
            sgn = 1 if c == 0 else -1
            own_p = P + 8 * c
            foe_p = P + 8 * (1 - c)
            # outpost: a knight on the 5th/6th/7th, pawn-supported and
            # beyond the reach of any enemy pawn, cannot be driven off
            for sq in pc[N + 8 * c]:
                f, r = sq & 7, sq >> 4
                if (r if c == 0 else 7 - r) < 4:
                    continue
                sr = r - 1 if c == 0 else r + 1
                cr = r + 1 if c == 0 else r - 1
                # both questions are "does an adjacent file hold a pawn one
                # rank under me / over me", which the hoisted masks answer in
                # one AND - no board read, no generator per knight
                supported = 0 <= sr < 8 and (pmnb[c][f] >> sr) & 1
                chased = 0 <= cr < 8 and (pmnb[1 - c][f] >> cr) & 1
                if supported and not chased:
                    mg += sgn * OUTPOST_MG
                    pg += sgn * OUTPOST_PG
            # a piece in the teeth of enemy pawns with no pawn of our own
            # behind it is chased at a tempo; two of them, with its exits
            # shutting, is not annoyance any more - it is being trapped.
            # Attacking and defending pawns stand on the very same two
            # squares either side of it, so one pass scores both
            ds = (-15, -17) if c == 0 else (15, 17)
            # the tuner priced HARASS at zero, and TRAPPED can only fire on a
            # slider, so as the weights stand this loop was walking every
            # knight and queen on the board to subtract nothing from the
            # score. Both branches stay written out, and the shortcut reads
            # the same two names, so it disappears the moment a bake moves
            # either of them
            if HARASS:
                tids = (N, B, R, Q)
            elif TRAPPED:
                tids = (B, R)
            else:
                tids = ()
            for tid in tids:
                slider = tid in (B, R)
                for sq in pc[tid + 8 * c]:
                    hits = backs = 0
                    for d in ds:
                        a = sq + d
                        if a & 0x88:
                            continue
                        q = b[a]
                        if q == own_p:
                            backs += 1
                        elif q == foe_p:
                            hits += 1
                    if hits and not backs:
                        mg -= sgn * HARASS
                        pg -= sgn * (HARASS // 2)
                        if slider and hits > 1 \
                                and _exits(b, sq, tid, 8 * c) <= 1:
                            mg -= sgn * TRAPPED
                            pg -= sgn * (TRAPPED // 2)
        if phase > 24:
            phase = 24
        if b0 >= 2:
            mg += BISHOPPAIR_MG
            pg += BISHOPPAIR_PG
        if b1 >= 2:
            mg -= BISHOPPAIR_MG
            pg -= BISHOPPAIR_PG

        # a bishop hemmed in by its own pawns on its colour is a bad one -
        # it bites on granite and cannot reach either wing
        for c in (0, 1):
            sign = 1 if c == 0 else -1
            for parity in bish_color[c]:
                n = pw_color[c][parity]
                if n >= 2:
                    mg -= sign * BADBISH_MG * (n - 1)
                    pg -= sign * BADBISH_PG * (n - 1)

        # pawn structure + open files. These terms are the reason the
        # evaluation out-priced the rest of the search put together: every one
        # of them was a generator walking a handful of rank lists to answer a
        # question about which ranks exist, and a byte answers it in one AND.
        # The lists themselves stay - they carry the doubled-pawn count and the
        # per-pawn walk - only the asking changes
        for c in (0, 1):
            sign = 1 if c == 0 else -1
            own = pawn_files[c]
            om = pmask[c]
            fm = pmask[1 - c]
            for f in range(8):
                ranks = own[f]
                nr = len(ranks)
                if nr > 1:
                    mg -= sign * DOUBLED_MG * (nr - 1)
                    pg -= sign * DOUBLED_PG * (nr - 1)
                nmask = pmnb[c][f]       # ranks the neighbour files hold
                if not nmask:
                    mg -= sign * ISOLATED_MG * nr
                    pg -= sign * ISOLATED_PG * nr
                bmask = pm3[1 - c][f]    # pawns that can stand in this file
                for r in ranks:
                    # a pawn backed by a neighbour is a chain rung: taking it
                    # costs the attacker a pawn on the spot, so the whole
                    # structure stands on these pairs
                    behind = r - 1 if c == 0 else r + 1
                    rung = 0 <= behind < 8 and (nmask >> behind) & 1
                    if rung:
                        mg += sign * SUPPORT
                        pg += sign * SUPPORT // 2
                    if bmask & (ABOVE[r + 1] if c == 0 else BELOW[r]):
                        # still blocked. backward: adjacent pawns exist but
                        # every one of them sits in FRONT of this one, so
                        # nothing can ever escort it forward - a weakness
                        if nmask and not (nmask & (BELOW[r + 1] if c == 0
                                                   else ABOVE[r])):
                            mg -= sign * BACKWARD_MG
                            pg -= sign * BACKWARD_PG
                        continue
                    rr = r if c == 0 else 7 - r
                    mg += sign * PASS_MG[rr]
                    pg += sign * PASS_PG[rr]
                    # graded escort bonus: a friendly pawn right behind
                    # lets the passer roll without any piece help; one
                    # level with it still backs the next push up
                    if rung:
                        mg += sign * ESCORT_MG
                        pg += sign * ESCORT_PG
                    elif (nmask >> r) & 1:
                        mg += sign * PHALANX_MG
                        pg += sign * PHALANX_PG
            for f in rook_file[c]:
                if not (om[f] | fm[f]):
                    mg += sign * OPENFILE_MG
                    pg += sign * OPENFILE_PG
                elif not om[f]:
                    mg += sign * SEMIFILE_MG
                    pg += sign * SEMIFILE_PG
            if seventh[c]:
                mg += sign * SEVENTH_MG * seventh[c]

        # king shield (middlegame only)
        for c in (0, 1):
            sign = 1 if c == 0 else -1
            ksq = self.kings[c]
            kf, kr = ksq & 7, ksq >> 4
            if phase > 8:
                for g in (kf - 1, kf, kf + 1):
                    if 0 <= g < 8:
                        front = kr + 1 if c == 0 else kr - 1
                        if not (0 <= front < 8 and (pmask[c][g] >> front) & 1):
                            mg -= sign * SHIELD1_MG
                        front2 = kr + 2 if c == 0 else kr - 2
                        if not (0 <= front2 < 8
                                and (pmask[c][g] >> front2) & 1):
                            mg -= sign * SHIELD2_MG

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
                    if not (pmask[c][g] >> front) & 1:
                        cage = False
                        break
                if cage:
                    mg -= sign * CAGE

        # king attack zone (middlegame only): every one of our pieces bearing
        # on the squares around the enemy king is mating pressure, and the
        # same count against us is danger - the classic king-safety term
        if phase > 8:
            for me in (0, 1):
                hits = _zone(b, pc, self.kings[me ^ 1], me)
                if me == 0:
                    mg += ZONE_MG * hits
                else:
                    mg -= ZONE_MG * hits

        score = (mg * phase + pg * (24 - phase)) // 24
        if self.side:
            score = -score
        score += TEMPO_MG                           # tempo for side to move

        # endgame driving: bare enemy king -> march ours to the edge and
        # bring our king close, or a flat material score shuffles forever
        for me in (0, 1):
            enemy = me ^ 1
            if nonpawns[me] and not nonpawns[enemy]:
                ef, er = self.kings[enemy] & 7, self.kings[enemy] >> 4
                to_edge = min(ef, 7 - ef) + min(er, 7 - er)
                kf, kr = self.kings[me] & 7, self.kings[me] >> 4
                drive = DRIVE_EDGE_MG * (6 - to_edge) + DRIVE_NEAR_MG * (
                    7 - max(abs(kf - ef), abs(kr - er)))
                score += drive if me == self.side else -drive
                break
        self._ecache[self.z] = score
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
        # reverse futility pruning: a shallow node whose static score already
        # sits above beta by more than its depth could reasonably add is a
        # fail-high before a single move has been generated - and generating and
        # ordering the moves is most of what such a node costs, which is the
        # whole point. Never in check, where the static score is not a lower
        # bound on anything, and never against a mate score, where beta is not
        # a score the evaluation has any opinion about. The value is kept: the
        # depth-1 futility below wants exactly this number.
        st = None
        if not in_check and depth <= RFP_DEPTH \
                and abs(beta) < MATE_THRESHOLD:
            st = self.evaluate()
            if st - RFP_MARGIN * depth >= beta:
                return st
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
        # it (feval stays None until then). At the depths the reverse-futility
        # test above has already paid for, though, the number is free.
        feval = st
        fm = 150
        lmp = 3 + depth * depth
        ek = self.kings[self.side ^ 1]     # the prunes below ask each dropped
        own8 = self.side << 3              # move whether it can still check
        for i, (_s, m) in enumerate(ordered):
            to = (m >> 8) & 255
            quiet = (not self.b[to] and not (m >> 16) & 7
                     and not (m >> 19) & F_EP)
            if best > -INF and not in_check and quiet:
                if depth <= 3 and i >= lmp:            # late-move pruning
                    if not _may_check(self.b, m, ek, own8):
                        continue
                if depth == 1:
                    if feval is None:
                        feval = self.evaluate()
                    if feval + fm < alpha \
                            and not _may_check(self.b, m, ek, own8):
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
                if len(self._ecache) > 200_000:
                    # stale scores cost a recompute and nothing else, so the
                    # memo is capped for memory rather than for correctness
                    self._ecache.clear()
        except TimeoutError:
            self.restore(snap)
        return best, best_score

    def progress(self, root, scores, best, best_score, sig_of, recent, seen=0):
        """Prefer a move that does not transpose onto an already-played spot.

        A plain two-player game is called a draw on the third arrival at the
        same position, and a search that sees only material reads the shuffle as
        free: nothing is lost by pacing back and forth, so a pawn-up engine will
        do it and halve the point. When the pick lands on a position the game
        has just seen, or the one now offered is close to that cap, the
        best-scoring move that stays fresh is swapped in, inside a margin that
        widens as the cap approaches. A finished game - a mate the search
        already sees - is never argued with."""
        if best is None or abs(best_score) >= MATE_THRESHOLD:
            return best
        if sig_of(best) not in recent and seen < PROGRESS_WARN:
            return best
        margin = EQUAL_CP + PROGRESS_SLACK * max(0, seen - 1)
        fresh = []
        for m in root:
            if m == best or sig_of(m) in recent:
                continue
            if scores.get(m, -INF) < best_score - margin:
                continue
            fresh.append((-scores.get(m, -INF), m))
        if not fresh:
            return best
        fresh.sort()
        return fresh[0][1]

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


def _sig_key(fen):
    """The repetition signature the game counts: pieces, turn, castling and en
    passant, the move counters dropped - exactly the first four FEN fields, the
    same key `ChessGame` keeps its tallies under."""
    return " ".join(fen.split()[:4])


def _as_move(position, moves, m):
    """The packed internal move as the board's own Move object, or None when
    the blurred view a fog engine played with has no twin on the real board."""
    frm, to = m & 255, (m >> 8) & 255
    want = ((frm & 15, frm >> 4), (to & 15, to >> 4),
            {5: "q", 4: "r", 3: "b", 2: "n"}.get((m >> 16) & 7))
    for mv in moves:
        if (mv.from_sq, mv.to_sq, mv.promotion) == want:
            return mv
    return None


def enforce_progress(eng, position, moves, scores, best, best_score):
    """Keep the engine moving forward so it never paces into its own draw.

    `best` comes back unchanged unless it lands on a position the game has just
    seen, or the one now offered is nearing the three-fold cap - then the
    strongest fresh move inside a widening margin takes it. Inert without the
    app's history: `seen`/`recent` are only filled for a plain classic board, so
    a fog or minefield game (whose true positions the bot cannot even see) and a
    hand-built test position keep exactly the move the search picked."""
    info = getattr(position, "info", None)
    if info is None or not scores:
        return best
    recent = frozenset(getattr(info, "recent", ()) or ())
    seen = getattr(info, "seen", 0) or 0
    if not recent and seen < PROGRESS_WARN:
        return best
    ext = {m: _as_move(position, moves, m) for m in scores}

    def sig_of(m):
        mv = ext.get(m)
        return None if mv is None else _sig_key(position.make(mv).fen())
    return eng.progress(list(scores), scores, best, best_score,
                        sig_of, recent, seen) or best


class FligBot(Bot):
    name = "fligbot classic 2p"
    author = "fligchess"
    version = 6                 # v6: the promotion of the lab copy - an opening
    #                             book plus a cheaper evaluation scan, so the
    #                             same budget reaches deeper (identical scores
    #                             at equal depth; see the eval mask rewrite).
    #                             With it: the quiet-move prune guard that closes
    #                             the mate-in-the-corner hole (`_may_check`) and
    #                             the repetition nudge that stops a won game
    #                             pacing back into a threefold draw.
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
        moved-piece `blunder_pass` - and then the repetition nudge, which
        trades a transposition for a forward move. None of the three invents a
        plan; they only refuse a pick the shallow search walked into, so
        tactics always win."""
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
        m = enforce_progress(eng, position, moves, eng.root_scores, m, score)
        frm = m & 255
        to = (m >> 8) & 255
        promo = {5: "q", 4: "r", 3: "b", 2: "n"}.get((m >> 16) & 7)
        mv = Move((frm & 15, frm >> 4), (to & 15, to >> 4), promo)
        return mv if mv in moves else random.choice(moves)


if __name__ == "__main__":
    # tiny self-test: python bots/fligbot_classic_2p.py
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
