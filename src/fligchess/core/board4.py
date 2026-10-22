"""Four-player chess: the standard cross board and its move generator.

This is a dedicated engine, kept entirely apart from the two-player
``board`` so the proven classic game, its perft and the bots are never
touched. The board is the usual cross: a central 8x8 with three rows of
eight cells pushed out of every side, 160 playable squares in all. Four
armies - white, black, red and blue - each keep a full standard chess set,
arranged along the home rank and pawn rank of their arm.

Rules implemented here (the free-for-all ruleset the project ships):

* A square is played as (x, y) with x the column and y the row, both 0..13.
  Row 0 is the north edge; white sits to the south, black to the north, red
  to the west and blue to the east.
* Pieces are two or three character codes: a kind letter (always uppercase,
  one of K Q R B N P) followed by the army id (``w``/``b``/``r``/``bl``), so
  ``"Pw"`` is a white pawn and ``"Kbl"`` a blue king. Case alone cannot name
  four armies, hence the explicit suffix.
* Pawns march toward the centre and on past it; a pawn promotes the instant
  it reaches the far army's home rank. There is no castling. En passant is
  possible only between directly opposing armies (white/black, red/blue).
* Turn order is white, black, red, blue. Elimination (a mated army losing
  its pieces) is a game-level concern; the board only reports whose turn it
  is and whether a king stands, and skips armies already marked out.
"""

ARMIES = ("w", "b", "r", "bl")      # the fixed turn order, white first
WHITE, BLACK, RED, BLUE = "w", "b", "r", "bl"
# which army each one is opposed by (en passant and, loosely, the front line)
OPPOSITE = {WHITE: BLACK, BLACK: WHITE, RED: BLUE, BLUE: RED}

SIZE = 14                           # the bounding grid is 14x14
CENTER_LO, CENTER_HI = 3, 10        # the central 8x8 spans these on both axes
KINDS = "KQRBNP"

KNIGHT_DELTAS = [(1, 2), (2, 1), (2, -1), (1, -2),
                 (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
BISHOP_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
ROOK_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
KING_DELTAS = BISHOP_DIRS + ROOK_DIRS

# Pawn mechanics per army: the forward step, the coordinate that starts and
# ends a pawn's journey, and which axis it travels along.
_PAWN = {
    WHITE: {"fwd": (0, -1), "start": 12, "promo": 0, "axis": "y"},
    BLACK: {"fwd": (0, 1), "start": 1, "promo": 13, "axis": "y"},
    RED: {"fwd": (1, 0), "start": 1, "promo": 13, "axis": "x"},
    BLUE: {"fwd": (-1, 0), "start": 12, "promo": 0, "axis": "x"},
}

# The back-rank order laid out along each army's home line. Rotating the
# whole start position by 90 degrees maps one army onto the next, so every
# army is equivalent and the four perft branches come out equal.
_BACK = ["R", "N", "B", "Q", "K", "B", "N", "R"]


def _victim_of(target, capturer):
    """The square holding the pawn an en passant capture on `target` takes.

    A two-square push ends one step past the square it passed, so the victim
    stands a further step along the capturer's own forward line - the same
    relation on the cross arms and the classic board, since opposing armies
    always face back along one axis."""
    x, y = target
    fwd = _PAWN[capturer]["fwd"]
    return (x - fwd[0], y - fwd[1])


START_CROSS = (
    "8/8/8/RNBQKBNR/PPPPPPPP/8/8/PPPPPPPP/RNBQKBNR/8/8/8 w - - 0 1")


def army_of(code):
    """The army id carried by a piece code ("Pbl" -> "bl")."""
    return code[1:]


def kind_of(code):
    """The kind letter of a piece code ("Pbl" -> "P")."""
    return code[0]


def playable(sq):
    """True for a cell that exists on the cross. The four 3x3 corner blocks
    cut out of the 14x14 bounding box are not squares."""
    x, y = sq
    if not (0 <= x < SIZE and 0 <= y < SIZE):
        return False
    centre = CENTER_LO <= x <= CENTER_HI and CENTER_LO <= y <= CENTER_HI
    north_south = (CENTER_LO <= x <= CENTER_HI
                   and (y <= CENTER_LO - 1 or y >= CENTER_HI + 1))
    east_west = (CENTER_LO <= y <= CENTER_HI
                 and (x <= CENTER_LO - 1 or x >= CENTER_HI + 1))
    return centre or north_south or east_west


def on_board(sq):
    x, y = sq
    return 0 <= x < SIZE and 0 <= y < SIZE


def sq_name(sq):
    """A cross coordinate as text: file letter then row number ("a1")."""
    return "abcdefghijklmn"[sq[0]] + str(sq[1] + 1)


def next_army(army, out=frozenset()):
    """The army that moves after `army`, skipping any already eliminated."""
    order = list(ARMIES)
    i = order.index(army)
    for step in range(1, len(order) + 1):
        cand = order[(i + step) % len(order)]
        if cand not in out:
            return cand
    return None


class Move:
    """One move on the cross. `promotion` names the piece (q/r/b/n) when a
    pawn reaches its far home rank."""

    __slots__ = ("from_sq", "to_sq", "promotion")

    def __init__(self, from_sq, to_sq, promotion=None):
        self.from_sq = from_sq
        self.to_sq = to_sq
        self.promotion = promotion

    def __eq__(self, other):
        return (isinstance(other, Move) and self.from_sq == other.from_sq
                and self.to_sq == other.to_sq
                and self.promotion == other.promotion)

    def __hash__(self):
        return hash((self.from_sq, self.to_sq, self.promotion))

    def __repr__(self):
        name = sq_name(self.from_sq) + "-" + sq_name(self.to_sq)
        if self.promotion:
            name += "=" + self.promotion.upper()
        return "Move(%s)" % name


class Board4:
    """The cross position plus the army to move. Squares are keyed by
    (x, y); every other army's pieces are simply not-mine."""

    def __init__(self):
        # grid[y][x] -> piece code or None; only playable cells ever hold one
        self.grid = [[None] * SIZE for _ in range(SIZE)]
        self.turn = WHITE
        self.ep = None                 # (to_sq, victim_sq) of a just-double pawn
        self.halfmove = 0
        self.fullmove = 1
        self.out = set()               # eliminated armies (no pieces, skipped)

    # ------------------------------------------------------------------ setup

    @classmethod
    def startpos(cls):
        """The standard four-army cross setup."""
        b = cls()
        files = range(CENTER_LO, CENTER_HI + 1)
        # white to the south, black to the north, red west, blue east
        for i, x in enumerate(files):
            b.set((x, 13), _BACK[i] + WHITE)
            b.set((x, 12), "P" + WHITE)
            b.set((x, 0), _BACK[i] + BLACK)
            b.set((x, 1), "P" + BLACK)
        for i, y in enumerate(files):
            b.set((0, y), _BACK[i] + RED)
            b.set((1, y), "P" + RED)
            b.set((13, y), _BACK[i] + BLUE)
            b.set((12, y), "P" + BLUE)
        b.turn = WHITE
        return b

    @classmethod
    def from_fen(cls, fen):
        b = cls()
        parts = fen.split()
        rows = parts[0].split("/")
        if len(rows) != SIZE:
            raise ValueError("cross FEN needs %d ranks" % SIZE)
        # row 0 of the FEN is the top of the board = grid y 13
        for i, row in enumerate(rows):
            y = SIZE - 1 - i
            x = 0
            for ch in row:
                if ch.isdigit():
                    x += int(ch)
                elif ch == ".":
                    x += 1
                else:
                    b.grid[y][x] = ch
                    x += 1
        b.turn = parts[1] if len(parts) > 1 else WHITE
        if len(parts) > 2 and parts[2] != "-":
            epfile = "abcdefghijklmn".index(parts[2][0])
            target = (epfile, int(parts[2][1:]) - 1)
            # the FEN names the square a capture lands on; the pawn it takes
            # stands one step further along the capturer's own forward line
            b.ep = (target, _victim_of(target, b.turn))
        if len(parts) > 4:
            b.halfmove = int(parts[4])
        if len(parts) > 5:
            b.fullmove = int(parts[5])
        return b

    def to_fen(self):
        rows = []
        for y in range(SIZE - 1, -1, -1):
            row = []
            for x in range(SIZE):
                if not playable((x, y)):
                    continue
                row.append(self.grid[y][x] or ".")
            rows.append("".join(row))
        ep = "-"
        if self.ep:
            ep = sq_name(self.ep[0])
        return ("/".join(rows) + " %s %s - %d %d"
                % (self.turn, ep, self.halfmove, self.fullmove))

    def clone(self):
        other = Board4.__new__(Board4)
        other.grid = [row[:] for row in self.grid]
        other.turn = self.turn
        other.ep = self.ep
        other.halfmove = self.halfmove
        other.fullmove = self.fullmove
        other.out = set(self.out)
        return other

    # ------------------------------------------------------------- accessors

    def get(self, sq):
        return self.grid[sq[1]][sq[0]]

    def set(self, sq, code):
        self.grid[sq[1]][sq[0]] = code

    def pieces_of(self, army):
        for y in range(SIZE):
            for x in range(SIZE):
                code = self.grid[y][x]
                if code is not None and army_of(code) == army:
                    yield (x, y), code

    def find_king(self, army):
        for (x, y), code in self.pieces_of(army):
            if kind_of(code) == "K":
                return (x, y)
        return None

    # ------------------------------------------------------- attack detection

    def is_attacked(self, sq, by):
        """True when `sq` is threatened by any piece of army `by`."""
        x, y = sq
        spec = _PAWN[by]
        if spec["axis"] == "y":
            dy = spec["fwd"][1]
            for dx in (-1, 1):
                if self.get_capturer((x + dx, y - dy), by, "P"):
                    return True
        else:
            dx = spec["fwd"][0]
            for dy in (-1, 1):
                if self.get_capturer((x - dx, y + dy), by, "P"):
                    return True
        for df, dr in KNIGHT_DELTAS:
            if self.get_capturer((x + df, y + dr), by, "N"):
                return True
        for df, dr in KING_DELTAS:
            if self.get_capturer((x + df, y + dr), by, "K"):
                return True
        for dirs, types in ((BISHOP_DIRS, "BQ"), (ROOK_DIRS, "RQ")):
            for df, dr in dirs:
                sx, sy = x + df, y + dr
                while playable((sx, sy)):
                    code = self.get((sx, sy))
                    if code is not None:
                        if army_of(code) == by and kind_of(code) in types:
                            return True
                        break
                    sx += df
                    sy += dr
        return False

    def get_capturer(self, sq, by, kind):
        """A piece of `by` of `kind` standing on `sq`, if there is one."""
        if not playable(sq):
            return False
        code = self.get(sq)
        return code is not None and army_of(code) == by and kind_of(code) == kind

    def in_check(self, army=None):
        army = army or self.turn
        king = self.find_king(army)
        if king is None:
            return False
        for other in ARMIES:
            if other != army and other not in self.out:
                if self.is_attacked(king, other):
                    return True
        return False

    # ---------------------------------------------------- move generation

    def pseudo_moves(self, army):
        moves = []
        for (x, y), code in self.pieces_of(army):
            self._piece_moves((x, y), code, moves)
        return moves

    def _piece_moves(self, sq, code, moves):
        army = army_of(code)
        kind = kind_of(code)
        x, y = sq
        if kind == "P":
            self._pawn_moves(sq, code, army, moves)
            return
        if kind == "N":
            deltas, slide = KNIGHT_DELTAS, False
        elif kind == "K":
            deltas, slide = KING_DELTAS, False
        elif kind == "B":
            deltas, slide = BISHOP_DIRS, True
        elif kind == "R":
            deltas, slide = ROOK_DIRS, True
        else:                                  # queen
            deltas, slide = KING_DELTAS, True
        for df, dr in deltas:
            if slide:
                sx, sy = x + df, y + dr
                while playable((sx, sy)):
                    if not self._push(moves, sq, (sx, sy), army):
                        break
                    sx += df
                    sy += dr
            else:
                if playable((x + df, y + dr)):
                    self._push(moves, sq, (x + df, y + dr), army)

    def _push(self, moves, frm, to, army):
        """Add a move onto `to` unless an ally blocks it. Returns whether the
        square was empty (so a slider may keep going)."""
        victim = self.get(to)
        if victim is not None and army_of(victim) == army:
            return False
        moves.append(Move(frm, to))
        return victim is None

    def _pawn_moves(self, sq, code, army, moves):
        spec = _PAWN[army]
        fx, fy = spec["fwd"]
        x, y = sq
        axis = spec["axis"]
        # single and double advance straight ahead
        one = (x + fx, y + fy)
        if playable(one) and self.get(one) is None:
            self._add_pawn(moves, sq, one, army, spec)
            coord = one[0] if axis == "x" else one[1]
            start = spec["start"]
            # the pawn's home line: white/black on y=12/1, red/blue on x=1/12
            home = sq[1] if axis == "y" else sq[0]
            at_home = (home == start)
            two = (one[0] + fx, one[1] + fy)
            if at_home and playable(two) and self.get(two) is None:
                moves.append(Move(sq, two))
        # diagonal captures, and en passant against the opposing army
        if axis == "y":
            lat = [(1, 0), (-1, 0)]
        else:
            lat = [(0, 1), (0, -1)]
        for lx, ly in lat:
            to = (x + fx + lx, y + fy + ly)
            if not playable(to):
                continue
            victim = self.get(to)
            if victim is not None and army_of(victim) != army:
                self._add_pawn(moves, sq, to, army, spec)
            elif (victim is None and self.ep is not None
                  and to == self.ep[0]
                  and self._ep_open(army)):
                moves.append(Move(sq, to))

    def _ep_open(self, army):
        """Whether the standing en passant window may be taken by `army`: the
        pawn that opened it must belong to the army `army` is opposed by, and
        still stand where the push left it."""
        victim = self._ep_victim(army)
        if victim is None or not playable(victim):
            return False
        code = self.get(victim)
        return (code is not None and kind_of(code) == "P"
                and army_of(code) == OPPOSITE[army])

    def _ep_victim(self, army):
        """The square the ep capture removes; recorded at the push, or worked
        out from the target when a position was read from text."""
        if self.ep is None:
            return None
        if len(self.ep) > 1 and self.ep[1] is not None:
            return self.ep[1]
        return _victim_of(self.ep[0], army)

    def _add_pawn(self, moves, frm, to, army, spec):
        coord = to[0] if spec["axis"] == "x" else to[1]
        if coord == spec["promo"]:
            for promo in "qrbn":
                moves.append(Move(frm, to, promo))
        else:
            moves.append(Move(frm, to))

    def legal_moves(self, army=None):
        """Moves for `army` (default the side to move) that leave its own
        king off every enemy's line of fire."""
        army = army or self.turn
        result = []
        for move in self.pseudo_moves(army):
            after = self.clone()
            after._apply(move, army)
            king = after.find_king(army)
            if king is None:
                continue
            if not any(after.is_attacked(king, other)
                       for other in ARMIES
                       if other != army and other not in after.out):
                result.append(move)
        return result

    # --------------------------------------------------------- move making

    def make_move(self, move):
        """Play `move` for the army that owns the piece, then hand over."""
        army = army_of(self.get(move.from_sq))
        captured = self._apply(move, army)
        self.turn = next_army(army, self.out)
        if self.turn == WHITE:
            self.fullmove += 1
        return captured

    def _apply(self, move, army):
        code = self.get(move.from_sq)
        kind = kind_of(code)
        fx0, fy0 = move.from_sq
        fx1, fy1 = move.to_sq
        captured = self.get(move.to_sq)
        capture_sq = move.to_sq

        # en passant: the captured pawn stands one step ahead of the target,
        # on the square the two-square push ended on
        if kind == "P" and captured is None and self.ep \
                and move.to_sq == self.ep[0] and self._ep_open(army):
            victim = self._ep_victim(army)
            captured = self.get(victim)
            self.set(victim, None)
            capture_sq = victim

        self.set(move.from_sq, None)
        if move.promotion:
            placed = move.promotion.upper() + army
        else:
            placed = code
        self.set(move.to_sq, placed)

        # en passant window: only a fresh two-square pawn push opens it
        spec = _PAWN[army]
        self.ep = None
        if kind == "P":
            coord0 = fx0 if spec["axis"] == "x" else fy0
            coord1 = fx1 if spec["axis"] == "x" else fy1
            if abs(coord1 - coord0) == 2:
                mid = ((fx0 + fx1) // 2, (fy0 + fy1) // 2)
                self.ep = (mid, move.to_sq)

        if kind == "P" or captured is not None:
            self.halfmove = 0
        else:
            self.halfmove += 1
        return captured

    # ---------------------------------------------------------- elimination

    def remove_army(self, army):
        """Take a mated army off the board: its pieces go and it is skipped
        from now on. Returns the squares it vacated."""
        vacated = []
        for (x, y), code in list(self.pieces_of(army)):
            self.set((x, y), None)
            vacated.append((x, y))
        self.out.add(army)
        if self.turn == army:
            self.turn = next_army(army, self.out)
        return vacated

    def alive_armies(self):
        return [a for a in ARMIES if a not in self.out]

    def has_king(self, army):
        return self.find_king(army) is not None

    def copy(self):
        return self.clone()

    def __repr__(self):
        return "Board4(%s)" % self.to_fen()
