"""fligbot classic 4p - the four-army search engine of FligChess.

`fligbot classic 2p` searches the classic 8x8. The cross board is a different
game - four armies on a 14x14, no castling, three rivals instead of one - so
it gets its own engine here: full alpha-beta over a flat cross array with
precomputed rays, a transposition table, killer/history ordering, a capture
quiescence, and a multi-army evaluation that grades a position as "us against
the rest", the strongest rival counted three times, the next two once and
twice. Elimination is part of the score too: an army with no move, or no king,
leaves the board, and the search sees the material that goes with it.

The same engine plays the two-player board as well. A classic position maps
into the middle of the cross - files a..h to columns 3..10, ranks 8..1 to rows
2..9 - so both shapes share the search, the table and the evaluation, and only
the layout data (the playable mask, the pawn specs and the castling squares)
differs. `bots/fligbot_classic_4p.py` is self-testing: run it and it checks
its own move generator against `Board4.legal_moves` and `Board.legal_moves`
by perft counts before it lets the bot answer.
"""

import pathlib
import random
import sys
import time

try:
    import fligchess.core                                  # noqa: F401
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                           / "src"))

from fligchess.core import Bot, Position, GameInfo          # noqa: E402
from fligchess.core.board4 import ARMIES as _IDS, playable as _playable4

# ------------------------------------------------------------------ shapes

SIZE = 14                          # the cross grid, and the box a 2P game
NSQ = SIZE * SIZE                  # lives in; 196 cells, 160 of them real
K, Q, R, B, N, P = 6, 5, 4, 3, 2, 1
EMPTY = 0

VAL = (0, 100, 320, 340, 500, 900, 20000)      # centipawns per kind
MATE = 1_000_000
MATE_THRESHOLD = MATE - 10_000
INF = 10 ** 9
MAXPLY = 40

EXACT, LOWER, UPPER = 0, 1, 2
F_EP, F_DOUBLE, F_PROMO, F_CASTLE = 1, 2, 4, 8

# narrowing: how many quiet moves a node past its first two plies may keep
QUIET_KEEP = 8
CUT = 800_000                     # ordering scores at or above this survive
SHIELD = 12                       # worth having in front of one's king
ADV = (0, 6, 12, 20, 30, 42, 56, 74, 96, 124, 160, 210, 280, 0)

CROSS, CENTRE = "cross", "centre"


def _idx(x, y):
    return y * SIZE + x


def _xy(sq):
    return sq % SIZE, sq // SIZE


# knight and king steps, and the eight ray directions, as (dx, dy)
_NDELTAS = ((1, 2), (2, 1), (2, -1), (1, -2),
            (-1, -2), (-2, -1), (-2, 1), (-1, 2))
_DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))
_STRAIGHT = ((1, 0), (-1, 0), (0, 1), (0, -1))
_DIRS = _DIAG + _STRAIGHT
_KDIRS = {B: (0, 1, 2, 3), R: (4, 5, 6, 7), Q: (0, 1, 2, 3, 4, 5, 6, 7)}
# which slider kinds sit at the end of each ray, in the order of _DIRS
_RAY_KINDS = ((0, (B, Q)), (1, (B, Q)), (2, (B, Q)), (3, (B, Q)),
              (4, (R, Q)), (5, (R, Q)), (6, (R, Q)), (7, (R, Q)))

# per army: the forward step, the sideways step of a capture, which axis it
# travels along and the home / promotion lines of that axis
_SPECS_CROSS = (
    ((0, -1), ((1, 0), (-1, 0)), "y", 12, 0),       # white marches north
    ((0, 1), ((1, 0), (-1, 0)), "y", 1, 13),        # black marches south
    ((1, 0), ((0, 1), (0, -1)), "x", 1, 13),        # red marches east
    ((-1, 0), ((0, 1), (0, -1)), "x", 12, 0),       # blue marches west
)
# the classic board mapped into the middle: the same two marches, but the
# pawns start and promote on the mapped ranks 1/7 and 6/0
_SPECS_CENTRE = (
    ((0, -1), ((1, 0), (-1, 0)), "y", 8, 2),
    ((0, 1), ((1, 0), (-1, 0)), "y", 3, 9),
    ((1, 0), ((0, 1), (0, -1)), "x", 1, 13),
    ((-1, 0), ((0, 1), (0, -1)), "x", 12, 0),
)

ARMY_OF = {a: i for i, a in enumerate(_IDS)}       # w, b, r, bl -> 0..3
ID_OF = tuple(_IDS)
OPPONENT = (1, 0, 3, 2)                             # opposed across the centre
PROMO_CHAR = {N: "n", B: "b", R: "r", Q: "q"}
CHAR_PROMO = {"n": N, "b": B, "r": R, "q": Q}


def _mask_cross():
    ok = bytearray(NSQ)
    for y in range(SIZE):
        for x in range(SIZE):
            if _playable4((x, y)):
                ok[_idx(x, y)] = 1
    return ok


def _mask_centre():
    """The classic 8x8 sitting in the middle of the cross grid.

    The mapping is file a..h -> column 3..10, rank 1..8 -> row 9..2 (row =
    9 - rank, the same convention `_load_classic` and `_castle_centre` use),
    so the eight playable rows are 2..9 and the eight files 3..10."""
    ok = bytearray(NSQ)
    for y in range(2, 10):
        for x in range(3, 11):
            ok[_idx(x, y)] = 1
    return ok


def _castle_centre():
    """Castling, for the mapped classic shape only: rights bit, king from and
    to, rook from and to, the squares that must be clear and the squares the
    king may not cross under fire."""
    def c(f, r):
        return _idx(f + 3, 9 - r)

    def rec(bit, kf, kt, rf, rt, empty, safe):
        return (bit, c(*kf), c(*kt), c(*rf), c(*rt),
                tuple(c(*s) for s in empty), tuple(c(*s) for s in safe))

    return (
        (rec(1, (4, 0), (6, 0), (7, 0), (5, 0),
             [(5, 0), (6, 0)], [(4, 0), (5, 0), (6, 0)]),
         rec(2, (4, 0), (2, 0), (0, 0), (3, 0),
             [(1, 0), (2, 0), (3, 0)], [(4, 0), (3, 0), (2, 0)])),
        (rec(4, (4, 7), (6, 7), (7, 7), (5, 7),
             [(5, 7), (6, 7)], [(4, 7), (5, 7), (6, 7)]),
         rec(8, (4, 7), (2, 7), (0, 7), (3, 7),
             [(1, 7), (2, 7), (3, 7)], [(4, 7), (3, 7), (2, 7)])),
        (), (),
    )


def _build(mask, specs, castle, rook_bits):
    """Every piece of geometry a node needs, precomputed for one shape.

    Squares are ints, so rays, knight hops, king steps and pawn steps are all
    stored as tuples of square indices; a node then reads the array and never
    does coordinate arithmetic again."""
    rays = [[()] * 8 for _ in range(NSQ)]
    kn = [()] * NSQ
    kg = [()] * NSQ
    pfrom = [[()] * NSQ for _ in range(4)]       # squares a pawn attacks into

    def inside(x, y):
        return 0 <= x < SIZE and 0 <= y < SIZE and mask[_idx(x, y)]

    pawn = []
    for a, (step, lat, axis, home, promo) in enumerate(specs):
        fx, fy = step
        f1 = [-1] * NSQ
        f2 = [-1] * NSQ
        caps = [()] * NSQ
        back = [-1] * NSQ
        at_home = bytearray(NSQ)
        at_promo = bytearray(NSQ)
        for y in range(SIZE):
            for x in range(SIZE):
                sq = _idx(x, y)
                if not mask[sq]:
                    continue
                coord = y if axis == "y" else x
                if coord == home:
                    at_home[sq] = 1
                if coord == promo:
                    at_promo[sq] = 1
                if inside(x + fx, y + fy):
                    f1[sq] = _idx(x + fx, y + fy)
                    if at_home[sq] and inside(x + 2 * fx, y + 2 * fy):
                        f2[sq] = _idx(x + 2 * fx, y + 2 * fy)
                hit = [(_idx(x + fx + lx, y + fy + ly)) for lx, ly in lat
                       if inside(x + fx + lx, y + fy + ly)]
                caps[sq] = tuple(hit)
                if inside(x - fx, y - fy):
                    back[sq] = _idx(x - fx, y - fy)
                got = pfrom[a][sq]
                for lx, ly in lat:
                    if inside(x - fx - lx, y - fy - ly):
                        got = got + (_idx(x - fx - lx, y - fy - ly),)
                pfrom[a][sq] = got
        pawn.append((f1, f2, caps, back, at_home, at_promo))
    for y in range(SIZE):
        for x in range(SIZE):
            sq = _idx(x, y)
            if not mask[sq]:
                continue
            rays[sq] = []
            for dx, dy in _DIRS:
                path = []
                nx, ny = x + dx, y + dy
                while inside(nx, ny):
                    path.append(_idx(nx, ny))
                    nx += dx
                    ny += dy
                rays[sq].append(tuple(path))
            kn[sq] = tuple(_idx(x + dx, y + dy) for dx, dy in _NDELTAS
                           if inside(x + dx, y + dy))
            kg[sq] = tuple(_idx(x + dx, y + dy) for dx, dy in _DIRS
                           if inside(x + dx, y + dy))
    return {"mask": mask, "rays": rays, "kn": kn, "kg": kg, "pfrom": pfrom,
            "pawn": pawn, "castle": castle, "rookbit": rook_bits,
            "pst": _pst(mask, specs), "shield": _shield(specs)}


def _shield(specs):
    """The squares a king on any square would like covered by its own pawns:
    the one straight ahead and the two diagonals in front of it."""
    out = []
    for step, lat, _axis, _home, _promo in specs:
        fx, fy = step
        table = [()] * NSQ
        for y in range(SIZE):
            for x in range(SIZE):
                got = []
                for dx, dy in ((fx, fy), (fx + lat[0][0], fy + lat[0][1]),
                               (fx + lat[1][0], fy + lat[1][1])):
                    if 0 <= x + dx < SIZE and 0 <= y + dy < SIZE:
                        got.append(_idx(x + dx, y + dy))
                table[_idx(x, y)] = tuple(got)
        out.append(table)
    return out


def _pst(mask, specs):
    """Piece-square play per army: sliders and knights want the middle of the
    board, pawns want to be further up their own arm."""
    tables = []
    for a, (step, _lat, axis, home, _promo) in enumerate(specs):
        fx, fy = step
        ahead = 1 if (fx > 0 if axis == "x" else fy > 0) else -1
        per = [[0] * NSQ for _ in range(7)]
        for y in range(SIZE):
            for x in range(SIZE):
                sq = _idx(x, y)
                if not mask[sq]:
                    continue
                cent = int(9 - max(abs(x - 6.5), abs(y - 6.5)))
                per[N][sq] = 6 * cent
                per[B][sq] = 5 * cent
                per[R][sq] = 4 * cent
                per[Q][sq] = 6 * cent
                coord = y if axis == "y" else x
                progress = (coord - home) * ahead
                if 0 <= progress < len(ADV):
                    per[P][sq] = ADV[progress]
        tables.append(per)
    return tables


SHAPES = {
    CROSS: _build(_mask_cross(), _SPECS_CROSS, (), [0] * NSQ),
    CENTRE: None,                              # filled in below
}
SHAPES[CENTRE] = _build(_mask_centre(), _SPECS_CENTRE, _castle_centre(),
                        [0] * NSQ)
_rook = SHAPES[CENTRE]["rookbit"]
for _a, _recs in enumerate(SHAPES[CENTRE]["castle"]):
    for _r in _recs:
        _rook[_r[3]] = _r[0]                   # which corner rook holds which right

# ------------------------------------------------------------------ zobrist

_rng = random.Random(0xC40554E5)
ZO = [[_rng.getrandbits(64) for _ in range(NSQ)] for _ in range(64)]
ZO_TURN = [_rng.getrandbits(64) for _ in range(4)]
ZO_EP = [[_rng.getrandbits(64) for _ in range(4)] for _ in range(NSQ)]
_CASTLE_BITS = [_rng.getrandbits(64) for _ in range(4)]
CASTLE_KEY = []
for _c in range(16):
    _k = 0
    for _i in range(4):
        if _c & (1 << _i):
            _k ^= _CASTLE_BITS[_i]
    CASTLE_KEY.append(_k)


def pack(frm, to, promo=0, flags=0):
    return frm | (to << 8) | (promo << 16) | (flags << 19)


def code_of(kind, army):
    return kind | (army << 3)


# ------------------------------------------------------------------ engine

class Engine:
    """The cross (or mapped classic) position, plus the search over it.

    Kept flat and integer-only: a node is array reads against precomputed
    square lists, so pure Python still reaches the tens of thousands of
    nodes a second that a four-army tree needs."""

    def __init__(self):
        self.b = [0] * NSQ
        self.sqs = [[], [], [], []]
        self.kings = [-1, -1, -1, -1]
        self.mat = [0, 0, 0, 0]
        self.tt = {}
        self.nodes = 0
        self.turn = 0
        self.me = 0
        self.n_live = 4
        self.ep = -1
        self.epa = -1
        self.castle = 0
        self.halfm = 0
        self.out = 0
        self.z = 0
        self.deadline = 0.0
        self.stopped = False
        self.killers = [[0, 0] for _ in range(MAXPLY)]
        self.hist = [[0] * NSQ for _ in range(4)]
        self.T = SHAPES[CROSS]
        self.shape = CROSS

    # ------------------------------------------------------------------ setup

    def load(self, board):
        """Read a position in, choosing the shape from the board it came on:
        anything with a `grid` is the cross, anything else is a classic 8x8
        mapped into the middle of one."""
        if hasattr(board, "grid"):
            self.shape = CROSS
            self.T = SHAPES[CROSS]
            self._load_grid(board)
        else:
            self.shape = CENTRE
            self.T = SHAPES[CENTRE]
            self._load_classic(board)
        self.n_live = len(self.standing())

    def _reset(self):
        self.b = [0] * NSQ
        self.sqs = [[], [], [], []]
        self.kings = [-1, -1, -1, -1]
        self.mat = [0, 0, 0, 0]
        self.ep = -1
        self.epa = -1
        self.castle = 0
        self.halfm = 0
        self.out = 0
        self.z = 0

    def _put(self, sq, piece):
        self.b[sq] = piece
        army = piece >> 3
        self.sqs[army].append(sq)
        self.mat[army] += VAL[piece & 7]
        if (piece & 7) == K:
            self.kings[army] = sq
        self.z ^= ZO[piece][sq]

    def _load_grid(self, board):
        """A cross board: `grid[y][x]` holds piece codes like "Pw", "Kbl"."""
        self._reset()
        grid = board.grid
        for y in range(SIZE):
            row = grid[y]
            for x in range(SIZE):
                text = row[x]
                if not text:
                    continue
                kind = {"P": P, "N": N, "B": B, "R": R, "Q": Q,
                        "K": K}[text[0]]
                self._put(_idx(x, y), code_of(kind, ARMY_OF[text[1:]]))
        self.turn = ARMY_OF[board.turn]
        for army in getattr(board, "out", ()):
            self.out |= 1 << ARMY_OF[army]
        if board.ep:
            self.ep = _idx(*board.ep[0])
            victim = board.ep[1] if len(board.ep) > 1 else None
            if victim is not None and self.b[_idx(*victim)]:
                self.epa = self.b[_idx(*victim)] >> 3
        self.halfm = board.halfmove
        self.z ^= ZO_TURN[self.turn]

    def _load_classic(self, board):
        """A classic board mapped in: file a..h -> column 3..10, rank 1..8 ->
        row 9..2. White and black are armies 0 and 1; the other two slots
        stand empty and are marked out, so the turn order collapses to w, b,
        w, b and the evaluation finds a single rival."""
        self._reset()
        self.out = (1 << 2) | (1 << 3)
        for rank in range(8):
            for file_ in range(8):
                ch = board.squares[rank][file_]
                if ch is None:
                    continue
                kind = {"P": P, "N": N, "B": B, "R": R, "Q": Q,
                        "K": K}[ch.upper()]
                army = 0 if ch.isupper() else 1
                self._put(_idx(file_ + 3, 9 - rank), code_of(kind, army))
        self.turn = 0 if board.turn == "w" else 1
        rights = 0
        for ch in getattr(board, "castling", ()):
            rights |= {"K": 1, "Q": 2, "k": 4, "q": 8}[ch]
        self.castle = rights
        if board.ep_target is not None:
            self.ep = _idx(board.ep_target[0] + 3, 9 - board.ep_target[1])
            self.epa = 1 - self.turn                # the side that just pushed
        self.halfm = board.halfmove
        self.z ^= ZO_TURN[self.turn] ^ CASTLE_KEY[rights]

    # -------------------------------------------------------------- geometry

    def live(self, army):
        return not (self.out >> army) & 1 and self.kings[army] >= 0

    def standing(self):
        return [a for a in range(4) if self.live(a)]

    def next_army(self, army):
        for step in (1, 2, 3):
            other = (army + step) & 3
            if self.live(other):
                return other
        return army

    def rivals(self, army):
        out = []
        for other in range(4):
            if other != army and self.live(other):
                out.append(other)
        return out

    def attacked(self, sq, by):
        """Whether army `by` reaches `sq`. Mirrors `Board4.is_attacked`."""
        b = self.b
        code = code_of(P, by)
        for t in self.T["pfrom"][by][sq]:
            if b[t] == code:
                return True
        code = code_of(N, by)
        for t in self.T["kn"][sq]:
            if b[t] == code:
                return True
        code = code_of(K, by)
        for t in self.T["kg"][sq]:
            if b[t] == code:
                return True
        rays = self.T["rays"][sq]
        for i in (0, 1, 2, 3):
            for t in rays[i]:
                p = b[t]
                if p:
                    if (p >> 3) == by and (p & 7) in (B, Q):
                        return True
                    break
        for i in (4, 5, 6, 7):
            for t in rays[i]:
                p = b[t]
                if p:
                    if (p >> 3) == by and (p & 7) in (R, Q):
                        return True
                    break
        return False

    def cheapest_attacker(self, sq, by):
        """The kind of the least valuable piece of `by` that reaches `sq`, or
        0 when it reaches nowhere. Used by the root safety pass."""
        b = self.b
        pawn = code_of(P, by)
        for t in self.T["pfrom"][by][sq]:
            if b[t] == pawn:
                return P
        best = 0
        for t in self.T["kn"][sq]:
            if b[t] == code_of(N, by):
                best = N
                break
        rays = self.T["rays"][sq]
        for i, kinds in _RAY_KINDS:
            for t in rays[i]:
                p = b[t]
                if p:
                    if (p >> 3) == by and (p & 7) in kinds:
                        kind = p & 7
                        if best == 0 or VAL[kind] < VAL[best]:
                            best = kind
                    break
        if best == 0:
            for t in self.T["kg"][sq]:
                if b[t] == code_of(K, by):
                    return K
        return best

    # ------------------------------------------------------- move generation

    def gen(self, army, caps=False):
        """Pseudo-legal packed moves for `army`, exactly the shapes
        `Board4.pseudo_moves` produces (and the classic ones, plus castling,
        on the mapped shape)."""
        b = self.b
        T = self.T
        moves = []
        f1, f2, pcaps, back, _home, promo = T["pawn"][army]
        rays = T["rays"]
        own_pawn = code_of(P, army)
        for sq in self.sqs[army]:
            piece = b[sq]
            kind = piece & 7
            if kind == P:
                t = f1[sq]
                if t >= 0 and not b[t]:
                    if promo[t]:
                        for piece_kind in (Q, R, B, N):
                            moves.append(pack(sq, t, piece_kind, F_PROMO))
                    elif not caps:
                        moves.append(pack(sq, t))
                    if not caps:
                        t2 = f2[sq]
                        if t2 >= 0 and not b[t2]:
                            moves.append(pack(sq, t2, 0, F_DOUBLE))
                for t in pcaps[sq]:
                    victim = b[t]
                    if victim and (victim >> 3) != army:
                        if promo[t]:
                            for piece_kind in (Q, R, B, N):
                                moves.append(pack(sq, t, piece_kind, F_PROMO))
                        else:
                            moves.append(pack(sq, t))
                    elif (not victim and t == self.ep
                          and self.epa == OPPONENT[army] and back[t] >= 0):
                        moves.append(pack(sq, t, 0, F_EP))
            elif kind in (N, K):
                for t in (T["kn"] if kind == N else T["kg"])[sq]:
                    victim = b[t]
                    if not victim:
                        if not caps:
                            moves.append(pack(sq, t))
                    elif (victim >> 3) != army:
                        moves.append(pack(sq, t))
            else:
                for i in _KDIRS[kind]:
                    for t in rays[sq][i]:
                        victim = b[t]
                        if not victim:
                            if not caps:
                                moves.append(pack(sq, t))
                            continue
                        if (victim >> 3) != army:
                            moves.append(pack(sq, t))
                        break
        if not caps and self.castle:
            self._castle(moves, army)
        return moves

    def _castle(self, moves, army):
        """King and rook both in place, the line clear, and no enemy covering
        the square the king stands on, or crosses."""
        for rec in self.T["castle"][army]:
            bit, kf, kt, rf, rt, empty, safe = rec
            if not self.castle & bit:
                continue
            if self.b[kf] != code_of(K, army) or self.b[rf] != code_of(R, army):
                continue
            if any(self.b[s] for s in empty):
                continue
            for other in self.rivals(army):
                if any(self.attacked(s, other) for s in safe):
                    break
            else:
                moves.append(pack(kf, kt, 0, F_CASTLE))

    def gen_legal(self, army, caps=False):
        """`Board4.legal_moves`' rule: a move is legal when the mover still
        has a king and none of the standing enemies reaches it afterwards."""
        if self.kings[army] < 0:
            return []
        enemies = self.rivals(army)
        out = []
        for m in self.gen(army, caps):
            u = self.make(m)
            king = self.kings[army]
            bad = False
            if king < 0:
                bad = True
            else:
                for other in enemies:
                    if self.attacked(king, other):
                        bad = True
                        break
            self.unmake(m, u)
            if not bad:
                out.append(m)
        return out

    # ------------------------------------------------------------ move making
    # `make` and `unmake` are purely mechanical, exactly like the board they
    # stand in for: they move pieces, take victims off, open and close the en
    # passant window and refresh the hash. Eliminating an army whose king was
    # captured is the game layer's business, so it lives in `_do`, which the
    # search uses to step down one ply.

    def make(self, m):
        b = self.b
        frm = m & 255
        to = (m >> 8) & 255
        pr = (m >> 16) & 7
        fl = (m >> 19) & 15
        piece = b[frm]
        kind = piece & 7
        army = piece >> 3
        ep_old, epa_old = self.ep, self.epa
        cas_old, half_old = self.castle, self.halfm
        king_old = self.kings[army]
        z = self.z ^ ZO[piece][frm]
        vic_sq = to
        victim = b[to]
        if fl & F_EP:
            # the pawn a capture takes stands one step past the square it
            # was aimed at, on the mover's own forward line
            vic_sq = self.T["pawn"][army][3][to]
            victim = b[vic_sq]
        if victim:
            va = victim >> 3
            z ^= ZO[victim][vic_sq]
            self.sqs[va].remove(vic_sq)
            self.mat[va] -= VAL[victim & 7]
            b[vic_sq] = 0
        placed = code_of(pr, army) if fl & F_PROMO else piece
        b[to] = placed
        b[frm] = 0
        self.sqs[army].remove(frm)
        self.sqs[army].append(to)
        if fl & F_PROMO:
            self.mat[army] += VAL[pr] - VAL[P]
        if kind == K:
            self.kings[army] = to
        z ^= ZO[placed][to]
        rook = None
        if fl & F_CASTLE:
            for rec in self.T["castle"][army]:
                if rec[2] == to:
                    rf, rt = rec[3], rec[4]
                    rp = b[rf]
                    b[rf] = 0
                    b[rt] = rp
                    self.sqs[army].remove(rf)
                    self.sqs[army].append(rt)
                    z ^= ZO[rp][rf] ^ ZO[rp][rt]
                    rook = (rf, rt, rp)
                    break
        if ep_old >= 0:
            z ^= ZO_EP[ep_old][epa_old]
        self.ep = -1
        self.epa = -1
        if fl & F_DOUBLE:
            self.ep = self.T["pawn"][army][0][frm]
            self.epa = army
            z ^= ZO_EP[self.ep][army]
        rights = cas_old
        if kind == K:
            rights &= ~(3 << (2 * army))
        elif kind == R:
            rights &= ~self.T["rookbit"][frm]
        if victim and (victim & 7) == R:
            rights &= ~self.T["rookbit"][vic_sq]
        z ^= CASTLE_KEY[rights] ^ CASTLE_KEY[cas_old]
        self.castle = rights
        self.halfm = 0 if kind == P or victim else half_old + 1
        self.z = z
        # 0 frm, 1 to, 2 piece, 3 placed, 4 victim square, 5 victim,
        # 6 own king square, 7 castling rights, 8 ep square, 9 ep army,
        # 10 halfmove clock, 11 rook hop, 12 took: the army whose king died
        return (frm, to, piece, placed, vic_sq, victim, king_old, cas_old,
                ep_old, epa_old, half_old, rook,
                (victim >> 3) if victim and (victim & 7) == K else -1)

    def unmake(self, m, u):
        b = self.b
        frm, to, piece, _placed, vic_sq, victim, king_old, cas_old = u[:8]
        ep_old, epa_old, half_old, rook = u[8], u[9], u[10], u[11]
        army = piece >> 3
        z = self.z ^ ZO[b[to]][to] ^ ZO[piece][frm]
        b[to] = 0
        b[frm] = piece
        self.sqs[army].remove(to)
        self.sqs[army].append(frm)
        if (m >> 19) & 15 & F_PROMO:
            self.mat[army] -= VAL[(m >> 16) & 7] - VAL[P]
        self.kings[army] = king_old
        if victim:
            va = victim >> 3
            b[vic_sq] = victim
            self.sqs[va].append(vic_sq)
            self.mat[va] += VAL[victim & 7]
            z ^= ZO[victim][vic_sq]
        if rook:
            rf, rt, rp = rook
            b[rt] = 0
            b[rf] = rp
            self.sqs[army].remove(rt)
            self.sqs[army].append(rf)
            z ^= ZO[rp][rt] ^ ZO[rp][rf]
        if self.ep >= 0:
            z ^= ZO_EP[self.ep][self.epa]
        z ^= CASTLE_KEY[cas_old] ^ CASTLE_KEY[self.castle]
        self.ep, self.epa, self.castle, self.halfm = (ep_old, epa_old, cas_old,
                                                      half_old)
        self.z = z

    def _do(self, m, army):
        """Play `m` for `army`, clear out an army that just lost its king and
        hand over. Returns (undo record, elimination snapshot, army to move)."""
        u = self.make(m)
        rec = None
        if u[12] >= 0:
            rec = self.eliminate(u[12])
        nxt = self.next_army(army)
        self.z ^= ZO_TURN[nxt] ^ ZO_TURN[army]
        self.turn = nxt
        return u, rec, nxt

    def _undo(self, m, u, army, nxt, rec):
        """The mirror of `_do`, in the opposite order."""
        self.z ^= ZO_TURN[army] ^ ZO_TURN[nxt]
        self.turn = army
        if rec is not None:
            self.relive(u[12], rec)
        self.unmake(m, u)

    def eliminate(self, army):
        """Take an eliminated army off the board the way `Game4` does: its
        pieces go, its squares empty and it is skipped from here on."""
        rec = []
        b = self.b
        for sq in self.sqs[army]:
            piece = b[sq]
            if piece:
                rec.append((sq, piece))
                b[sq] = 0
                self.z ^= ZO[piece][sq]
        king = self.kings[army]
        self.sqs[army] = []
        self.mat[army] = 0
        self.kings[army] = -1
        self.out |= 1 << army
        self.n_live -= 1
        return rec, king

    def relive(self, army, snap):
        rec, king = snap
        b = self.b
        total = 0
        for sq, piece in rec:
            b[sq] = piece
            self.sqs[army].append(sq)
            total += VAL[piece & 7]
            self.z ^= ZO[piece][sq]
        self.mat[army] = total
        self.kings[army] = king
        self.out &= ~(1 << army)
        self.n_live += 1

    # ------------------------------------------------------------ evaluation

    def strength(self):
        """What each standing army is worth: material kept up to date move by
        move, piece-square play from the shape's tables, and the pawns
        covering the king."""
        b = self.b
        out = [0, 0, 0, 0]
        for army in range(4):
            if not self.live(army):
                continue
            table = self.T["pst"][army]
            total = self.mat[army]
            for sq in self.sqs[army]:
                total += table[b[sq] & 7][sq]
            out[army] = total + self.shelter(army)
        return out

    def shelter(self, army):
        """Reward a king still behind its pawns, and punish one that has walked
        out in front of nothing."""
        king = self.kings[army]
        if king < 0:
            return 0
        own = code_of(P, army)
        have = sum(1 for sq in self.T["shield"][army][king] if self.b[sq] == own)
        return SHIELD * (2 * have - 3)

    def evaluate(self):
        """The position as the root army sees it, judged against every rival
        still on the board. Each pairing counts, but not equally: the
        strongest rival three times, the next two and one, so the engine
        always works on the game it is actually losing rather than on the
        army it happens to be ahead of."""
        me = self.me
        score = self.strength()
        rest = sorted((score[a] for a in range(4)
                       if a != me and self.live(a)), reverse=True)
        total = 0
        for weight, rival in zip((4, 2, 1), rest):
            total += weight * (score[me] - rival)
        return total // 4

    # ------------------------------------------------------------- ordering

    def order(self, moves, army, hint, ply):
        """Moves as (score, move), best first: the table's own move, then
        captures priced by what they take less what they send, promotions,
        killers and the per-army history.

        Everything that forces an answer - a capture, an ep, a promotion -
        lands at or above `CUT`, which is the line `narrow` later draws."""
        b = self.b
        killers = self.killers[ply]
        hist = self.hist[army]
        scored = []
        for m in moves:
            frm = m & 255
            to = (m >> 8) & 255
            vic = b[to]
            pr = (m >> 16) & 7
            if vic:
                score = CUT + 16 * VAL[vic & 7] - min(VAL[b[frm] & 7], VAL[Q])
            elif (m >> 19) & 15 & F_EP:
                score = CUT + 16 * VAL[P] - VAL[P]
            elif pr:
                score = CUT + 16 * VAL[pr]
            else:
                score = 0
            if score < CUT:
                if m == hint:
                    score = 4_000_000
                elif m == killers[0]:
                    score = 400_000
                elif m == killers[1]:
                    score = 390_000
                else:
                    score = hist[to]
            scored.append((score, m))
        scored.sort(reverse=True)
        return scored

    @staticmethod
    def narrow(scored):
        """Every capture and promotion, plus the best few quiet moves: the
        cross branches wide enough that a four-army tree cannot afford the
        quiet tail past its first plies."""
        out = []
        quiet = 0
        for score, m in scored:
            if score >= CUT or quiet < QUIET_KEEP:
                out.append((score, m))
            if score < CUT:
                quiet += 1
        return out

    def note(self, m, army, score, ply, depth):
        """A quiet move that caused a cutoff earns its place in the killer and
        history lists; captures are already ordered by what they win."""
        if score >= CUT:
            return
        slot = self.killers[ply]
        if slot[0] != m:
            slot[1] = slot[0]
            slot[0] = m
        to = (m >> 8) & 255
        hist = self.hist[army]
        bonus = depth * depth * 100
        if hist[to] < 300_000:
            hist[to] += bonus

    # ---------------------------------------------------------------- search

    def terminal(self, ply):
        """The score of a decided node, or None while the game runs: no king
        of ours left, or nobody else's."""
        if self.kings[self.me] < 0:
            return -MATE + ply
        if self.n_live == 1:
            return MATE - ply
        return None

    def ab(self, army, depth, alpha, beta, ply):
        """Alpha-beta over four armies, scored from the root army's side.

        Our own army is the maximising player and every rival together the
        minimising one - the standard two-player reading of a multiplayer
        game - which is what makes the window meaningful at all: a rival node
        fails low when the coalition can hold us down, whoever of them gets to
        push the button."""
        self.nodes += 1
        if self.stopped:
            return 0
        if not self.nodes & 1023 and time.perf_counter() > self.deadline:
            self.stopped = True
            return 0
        me = self.me
        over = self.terminal(ply)
        if over is not None:
            return over
        if depth <= 0 or ply >= MAXPLY - 1:
            return self.qsearch(army, alpha, beta, ply)
        ours = army == me
        entry = self.tt.get(self.z)
        hint = 0
        if entry is not None:
            e_depth, flag, value, e_move = entry
            hint = e_move
            if e_depth >= depth:
                if flag == EXACT:
                    return value
                if ours:
                    if flag == LOWER and value > alpha:
                        alpha = value
                elif flag == UPPER and value < beta:
                    beta = value
                if alpha >= beta:
                    return value
        moves = self.gen_legal(army)
        if not moves:
            if ours:
                return -MATE + ply           # we are mated or stalemated
            # the layer clears an army that cannot move: score the board with
            # it gone, then put it back exactly as the node found it
            snap = self.eliminate(army)
            try:
                value = MATE - ply if self.n_live == 1 else self.evaluate()
            finally:
                self.relive(army, snap)
            return value
        scored = self.order(moves, army, hint, ply)
        if depth >= 3 and ply >= 2:
            scored = self.narrow(scored)
        best_move = 0
        orig = alpha if ours else beta
        value = -INF if ours else INF
        for _score, m in scored:
            u, rec, nxt = self._do(m, army)
            child = self.ab(nxt, depth - 1, alpha, beta, ply + 1)
            self._undo(m, u, army, nxt, rec)
            if self.stopped:
                return value
            if ours:
                if child > value:
                    value = child
                    best_move = m
                    if value > alpha:
                        alpha = value
                    if value >= beta:
                        self.note(m, army, _score, ply, depth)
                        break
            else:
                if child < value:
                    value = child
                    best_move = m
                    if value < beta:
                        beta = value
                    if value <= alpha:
                        self.note(m, army, _score, ply, depth)
                        break
        if len(self.tt) > 200_000:
            self.tt.clear()
        if ours:
            # a max node that failed high proved a floor on the position; one
            # that searched everything and never beat its own bound proved a
            # ceiling; only a value inside the window is the true score
            if value >= beta:
                flag = LOWER
            elif value <= orig:
                flag = UPPER
            else:
                flag = EXACT
        else:
            if value <= alpha:
                flag = UPPER
            elif value >= orig:
                flag = LOWER
            else:
                flag = EXACT
        self.tt[self.z] = (depth, flag, value, best_move)
        return value

    def qsearch(self, army, alpha, beta, ply):
        """Capture resolution at the leaves. Without it the search is blind at
        the horizon: it would cheerfully walk a queen onto a square it only
        stops seeing one ply later."""
        self.nodes += 1
        if self.stopped:
            return 0
        if not self.nodes & 1023 and time.perf_counter() > self.deadline:
            self.stopped = True
            return 0
        me = self.me
        ours = army == me
        over = self.terminal(ply)
        if over is not None:
            return over
        stand = self.evaluate()
        if ours:
            if stand >= beta:
                return stand
            value = stand
            if stand > alpha:
                alpha = stand
        else:
            if stand <= alpha:
                return stand
            value = stand
            if stand < beta:
                beta = stand
        if ply >= MAXPLY - 1:
            return value
        moves = self.gen_legal(army, caps=True)
        if not moves:
            return value
        for _score, m in self.order(moves, army, 0, ply):
            vic = self.b[(m >> 8) & 255]
            gain = VAL[vic & 7] if vic else VAL[P]
            if ours and stand + gain + 80 < alpha:
                continue                     # the harvest cannot matter
            u, rec, nxt = self._do(m, army)
            child = self.qsearch(nxt, alpha, beta, ply + 1)
            self._undo(m, u, army, nxt, rec)
            if self.stopped:
                return value
            if ours:
                if child > value:
                    value = child
                    if value > alpha:
                        alpha = value
                    if value >= beta:
                        break
            elif child < value:
                value = child
                if value < beta:
                    beta = value
                if value <= alpha:
                    break
        return value

    def root_pass(self, scored, depth):
        """One full iteration at the root, every move on its own window.

        The root scores are then exact rather than bounds, which is what
        lets `guard` compare a pick with its rivals on their merits."""
        scores = {}
        best = None
        best_score = -INF
        for _score, m in scored:
            u, rec, nxt = self._do(m, self.me)
            value = self.ab(nxt, depth - 1, -INF, INF, 1)
            self._undo(m, u, self.me, nxt, rec)
            if self.stopped:
                return None, -INF, {}
            scores[m] = value
            if value > best_score:
                best, best_score = m, value
        return best, best_score, scores

    def search(self, me, budget, maxdepth):
        """Iterative deepening for `me` inside `budget` seconds: returns
        (move, score, {move: score})."""
        self.me = me
        self.turn = me
        self.nodes = 0
        self.stopped = False
        self.deadline = time.perf_counter() + budget
        self.killers = [[0, 0] for _ in range(MAXPLY)]
        self.hist = [[0] * NSQ for _ in range(4)]
        moves = self.gen_legal(me)
        if not moves:
            return None, -INF, {}
        scored = self.order(moves, me, 0, 0)
        best = best_score = None
        scores = {}
        depth = 0
        try:
            while depth < maxdepth:
                result = self.root_pass(scored, depth + 1)
                if result[0] is None:
                    break
                best, best_score, scores = result
                depth += 1
                scored = self.order([m for _s, m in scored], me, best, 0)
                if time.perf_counter() > self.deadline:
                    break
                if abs(best_score) >= MATE_THRESHOLD:
                    break
                if len(self.tt) > 300_000:
                    self.tt.clear()
        except TimeoutError:
            pass
        return best, best_score, scores

    # --------------------------------------------------------- safety at root

    def hang(self, m):
        """What the standing enemies gain by retaking on the square `m` puts
        its piece on: the whole piece when nothing of ours covers it, the
        difference against the cheapest attacker when something does."""
        u = self.make(m)
        try:
            to = (m >> 8) & 255
            piece = self.b[to]
            if not piece or (piece & 7) == K:
                return 0
            army = piece >> 3
            covered = any(self.attacked(to, army) for _ in (0,))
            worst = 0
            for other in self.rivals(army):
                who = self.cheapest_attacker(to, other)
                if not who:
                    continue
                gain = VAL[piece & 7] - (VAL[who] if covered else 0)
                if gain > worst:
                    worst = gain
            return worst
        finally:
            self.unmake(m, u)

    def guard(self, root, scores, best, best_score, hang=None):
        """Refuse a pick that hands the piece it just moved straight over.

        A four-army tree is shallow, and shallow trees step onto things. Only
        moves the root already rated within `ACCEPT_CP` of the pick are
        considered, so a genuine tactic is never argued with, and nothing here
        invents a plan: it declines a gift and takes the equally good square
        that stays safe instead."""
        if best is None or abs(best_score) >= MATE_THRESHOLD:
            return best
        if hang is None:
            hang = {m: self.hang(m) for m in root}
        worst = hang[best]
        if worst < HANG_MIN:
            return best
        rivals = []
        for m in root:
            if m == best or hang[m] >= worst:
                continue
            if scores.get(m, -INF) < best_score - ACCEPT_CP:
                continue
            rivals.append((hang[m], -scores.get(m, -INF), m))
        if not rivals:
            return best
        rivals.sort()
        for _hang, _neg, m in rivals[:VERIFY_CAP]:
            return m
        return best

    def progress(self, root, scores, best, best_score, sig_of, recent, seen=0):
        """Prefer a move that does not transpose onto an already-played spot.

        The cross game eliminates the army handed the same position for the
        fourth time, so replaying a square is not a draw - it is a self-inflicted
        loss. When the search's pick lands on a position the game has played
        recently, or the one now offered to us is nearing that limit, this swaps
        in the best-scoring move that stays fresh, within a margin that widens
        as we approach the cap. A finished game (a mate the search already
        sees) is never argued with."""
        if best is None or abs(best_score) >= MATE_THRESHOLD:
            return best
        if sig_of(best) not in recent and seen < PROGRESS_WARN:
            return best
        margin = ACCEPT_CP + PROGRESS_SLACK * max(0, seen - 1)
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


# ------------------------------------------------------------------ the bot

HANG_MIN = 150            # more than a pawn: a gambit is not a blunder
ACCEPT_CP = 60            # how close to the pick a safer rival must be
VERIFY_CAP = 4            # how many rivals the pass will swap in front of
PROGRESS_WARN = 2         # seen count at which we stop trusting the shuffle
PROGRESS_SLACK = 120      # extra centipawns a forward move may cost per count


def _sig_key(fen):
    """The repetition signature the cross game counts: pieces, turn and en
    passant, the move counters dropped - exactly the first four FEN fields."""
    return " ".join(fen.split()[:4])


def _cross_move(position, moves, m):
    """The packed internal cross move as the board's own Move, or None."""
    want = (_xy(m & 255), _xy((m >> 8) & 255), PROMO_CHAR.get((m >> 16) & 7))
    for mv in moves:
        if (mv.from_sq, mv.to_sq, mv.promotion) == want:
            return mv
    return None


def enforce_progress(eng, position, moves, scores, best, best_score, cross):
    """Keep the bot moving forward so it never walks into its own repetition
    loss. A cross game eliminates the army handed the same position a fourth
    time, so a transposition back onto a played square is a self-inflicted
    loss, not a draw. `best` is returned unchanged unless it lands on a
    recent position (or we are near our own cap), when the best fresh move in
    a widening margin is swapped in. Inert without app history: a two-player
    game draws differently, and fog hides the true positions."""
    if not cross or not scores:
        return best
    info = position.info
    if info is None:
        return best
    recent = frozenset(getattr(info, "recent", ()) or ())
    seen = getattr(info, "seen", 0) or 0
    if not recent and seen < PROGRESS_WARN:
        return best
    ext = {m: _cross_move(position, moves, m) for m in scores}

    def sig_of(m):
        mv = ext.get(m)
        return None if mv is None else _sig_key(position.make(mv).fen())
    return eng.progress(list(scores), scores, best, best_score,
                        sig_of, recent, seen) or best


class FligBot4P(Bot):
    name = "fligbot classic 4p"
    author = "fligchess"
    version = 4                 # v4: seats the cross only; the mapped 2P shape
    #                             stays in the engine for the bots that import
    #                             it (blitz, fog), it is simply not offered as a
    #                             two-player opponent any more - `fligbot
    #                             classic 2p` and `fligbot blitz` cover those
    CONFIG = {}                 # no per-seat options: it always plays to win
    MODES = ("4p/*/classic", "4p/*/minefield")     # the cross board only
    says = True                 # it announces a finished game through chat

    budget = 0.8                # seconds per move; the app's --budget overrides
    MAX_SEARCH_DEPTH = 24

    def __init__(self):
        super().__init__()
        self._engine = Engine()

    # ------------------------------------------------------------- search

    def choose(self, position):
        """Its strongest move: iterative deepening within `self.budget`, the
        capture quiescence resolving every leaf, and then the root safety
        pass, which takes the pick off a square the enemies walk in on for
        free. In a game it has already won it says so in the chat."""
        moves = position.legal_moves()
        if not moves:
            return None
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
            m = enforce_progress(eng, position, moves, scores, m,
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


def _perft(eng, army, depth):
    """Node count for `army` to `depth`, using the engine's own legality and
    make/undo; the self-test compares it against the real boards' counts."""
    if depth == 0:
        return 1
    total = 0
    for m in eng.gen_legal(army):
        nxt = eng.next_army(army)
        u, rec, _ = eng._do(m, army)
        total += _perft(eng, nxt, depth - 1)
        eng._undo(m, u, army, nxt, rec)
    return total


if __name__ == "__main__":
    # tiny self-test: python bots/fligbot_classic_4p.py
    from fligchess.core.board import Board as _Board
    from fligchess.core.board4 import Board4 as _Board4
    eng = Engine()
    # the mapped classic shape must generate exactly the real board's moves
    for label, fen, counts in (
            ("startpos", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
             (20, 400, 8902, 197281)),
            ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
             (48, 2039, 97862))):
        eng.load(_Board.from_fen(fen))
        eng.n_live = len(eng.standing())
        got = [_perft(eng, 0, d) for d in range(1, len(counts) + 1)]
        print(label, "perft", got, "expected", list(counts),
              "OK" if got == list(counts) else "MISMATCH")
    # the cross shape against Board4
    board4 = _Board4.startpos()
    eng.load(board4)
    eng.n_live = len(eng.standing())
    got = [_perft(eng, 0, d) for d in (1, 2)]
    exp0 = len(board4.legal_moves())
    print("cross perft", got, "expected root", exp0,
          "OK" if got[0] == exp0 else "MISMATCH")
    # the bot answers on a real 2P and 4P position with a legal move
    bot = FligBot4P()
    bot.configure({})
    bot.budget = 0.4
    p2 = Position(fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    mv = bot.choose(p2)
    print(f"{bot.name} 2p picks:", mv, "legal" if mv in p2.legal_moves() else "ILLEGAL")
    p4 = Position(board=_Board4.startpos(),
                  info=GameInfo(players=4, army="w"))
    mv = bot.choose(p4)
    print(f"{bot.name} 4p picks:", mv, "legal" if mv in p4.legal_moves() else "ILLEGAL")
    # the root safety pass: with a black pawn on e4 covering d3, a queen
    # walked from d1 to d3 is handed straight over; the guard must refuse it
    # when the root rates a safe square equally. Same audit as fligbot's.
    eng.load(_Board.from_fen("4k3/8/8/8/4p3/8/8/3QK3 w - - 0 1"))
    eng.n_live = len(eng.standing())
    eng.me = eng.turn = 0
    legal = eng.gen_legal(0)
    pull = pack(_idx(6, 9), _idx(6, 7))        # Qd1-d3, onto the pawn's gun
    hang = {m: eng.hang(m) for m in legal}
    safe = any(hang[m] < HANG_MIN for m in legal if m != pull)
    keep = eng.guard(legal, {m: 0 for m in legal}, pull, 0, hang)
    print("blunder guard keeps the queen off d3:", hang[pull] >= HANG_MIN
          and safe and keep != pull)
    # the progress filter: on the cross, a pick that transposes back onto a
    # position the game has played is swapped for a fresh one; with no history
    # it leaves the search's own choice well alone
    eng.load(_Board4.startpos())
    eng.n_live = len(eng.standing())
    eng.me = eng.turn = 0
    legal4 = eng.gen_legal(0)
    pick4 = legal4[0]
    scores4 = {m: 0 for m in legal4}
    seen_sig = lambda m: "OLD" if m == pick4 else "NEW%d" % m
    swapped = eng.progress(legal4, scores4, pick4, 0, seen_sig, {"OLD"})
    untouched = eng.progress(legal4, scores4, pick4, 0, seen_sig, set())
    print("progress swaps a repeat for a fresh move, else leaves it:",
          swapped != pick4 and swapped in legal4 and untouched == pick4)
