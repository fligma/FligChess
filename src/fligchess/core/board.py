"""Chess board state and move generation.

Squares are (file, rank) tuples with file 0 = a-file, rank 0 = 1st rank
(white's home rank). Pieces are stored as single characters: uppercase for
white (K, Q, R, B, N, P), lowercase for black (k, q, r, b, n, p).
"""

WHITE, BLACK = "w", "b"

KNIGHT_DELTAS = [(1, 2), (2, 1), (2, -1), (1, -2),
                 (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
BISHOP_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
ROOK_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
KING_DELTAS = BISHOP_DIRS + ROOK_DIRS

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def color_of(piece):
    return WHITE if piece.isupper() else BLACK


def on_board(sq):
    f, r = sq
    return 0 <= f < 8 and 0 <= r < 8


class Move:
    """A single move. `promotion` is set (e.g. 'q') for promotion moves."""

    __slots__ = ("from_sq", "to_sq", "promotion")

    def __init__(self, from_sq, to_sq, promotion=None):
        self.from_sq = from_sq
        self.to_sq = to_sq
        self.promotion = promotion

    def __eq__(self, other):
        return (isinstance(other, Move) and self.from_sq == other.from_sq
                and self.to_sq == other.to_sq and self.promotion == other.promotion)

    def __hash__(self):
        return hash((self.from_sq, self.to_sq, self.promotion))

    def __repr__(self):
        name = sq_name(self.from_sq) + sq_name(self.to_sq)
        if self.promotion:
            name += self.promotion
        return f"Move({name})"


def sq_name(sq):
    return "abcdefgh"[sq[0]] + str(sq[1] + 1)


class Board:
    def __init__(self):
        # squares[rank][file] -> piece char or None
        self.squares = [[None] * 8 for _ in range(8)]
        self.turn = WHITE
        self.castling = set("KQkq")
        self.ep_target = None      # (file, rank) an en passant capture may land on
        self.halfmove = 0          # moves since last capture / pawn push
        self.fullmove = 1

    # ------------------------------------------------------------------ setup

    @classmethod
    def from_fen(cls, fen):
        board = cls()
        parts = fen.split()
        rows = parts[0].split("/")
        for i, row in enumerate(rows):
            rank = 7 - i
            file_ = 0
            for ch in row:
                if ch.isdigit():
                    file_ += int(ch)
                else:
                    board.squares[rank][file_] = ch
                    file_ += 1
        board.turn = WHITE if parts[1] == "w" else BLACK
        board.castling = set() if parts[2] == "-" else set(parts[2])
        board.ep_target = None if parts[3] == "-" else (
            "abcdefgh".index(parts[3][0]), int(parts[3][1]) - 1)
        board.halfmove = int(parts[4])
        board.fullmove = int(parts[5])
        return board

    def clone(self):
        other = Board.__new__(Board)
        other.squares = [row[:] for row in self.squares]
        other.turn = self.turn
        other.castling = set(self.castling)
        other.ep_target = self.ep_target
        other.halfmove = self.halfmove
        other.fullmove = self.fullmove
        return other

    def to_fen(self):
        rows = []
        for rank in range(7, -1, -1):
            row, empty = "", 0
            for file_ in range(8):
                piece = self.squares[rank][file_]
                if piece is None:
                    empty += 1
                else:
                    if empty:
                        row += str(empty)
                        empty = 0
                    row += piece
            if empty:
                row += str(empty)
            rows.append(row)
        cast = "".join(c for c in "KQkq" if c in self.castling) or "-"
        ep = "-" if self.ep_target is None else sq_name(self.ep_target)
        return ("/".join(rows) + f" {self.turn} {cast} {ep} "
                + f"{self.halfmove} {self.fullmove}")

    # ------------------------------------------------------------- accessors

    def get(self, sq):
        return self.squares[sq[1]][sq[0]]

    def set(self, sq, piece):
        self.squares[sq[1]][sq[0]] = piece

    def find_king(self, color):
        king = "K" if color == WHITE else "k"
        for rank in range(8):
            for file_ in range(8):
                if self.squares[rank][file_] == king:
                    return (file_, rank)
        return None

    # ------------------------------------------------------- attack detection

    def is_attacked(self, sq, by):
        """True if `sq` is attacked by any piece of color `by`."""
        f, r = sq
        # pawns
        pr = r - 1 if by == WHITE else r + 1
        for pf in (f - 1, f + 1):
            if on_board((pf, pr)) and self.get((pf, pr)) == ("P" if by == WHITE else "p"):
                return True
        # knights
        kn = "N" if by == WHITE else "n"
        for df, dr in KNIGHT_DELTAS:
            if on_board((f + df, r + dr)) and self.get((f + df, r + dr)) == kn:
                return True
        # king
        kg = "K" if by == WHITE else "k"
        for df, dr in KING_DELTAS:
            if on_board((f + df, r + dr)) and self.get((f + df, r + dr)) == kg:
                return True
        # sliding pieces
        for dirs, types in ((BISHOP_DIRS, "BQbq"), (ROOK_DIRS, "RQrq")):
            for df, dr in dirs:
                sf, sr = f + df, r + dr
                while on_board((sf, sr)):
                    piece = self.get((sf, sr))
                    if piece is not None:
                        if piece in types and color_of(piece) == by:
                            return True
                        break
                    sf += df
                    sr += dr
        return False

    def in_check(self, color=None):
        color = color or self.turn
        king = self.find_king(color)
        enemy = BLACK if color == WHITE else WHITE
        return king is not None and self.is_attacked(king, enemy)

    # ---------------------------------------------------- move generation

    def pseudo_legal_moves(self, captures_only=False):
        """All moves for the side to move, ignoring king safety."""
        moves = []
        color = self.turn
        for rank in range(8):
            for file_ in range(8):
                piece = self.squares[rank][file_]
                if piece is None or color_of(piece) != color:
                    continue
                self._piece_moves((file_, rank), piece, moves, captures_only)
        return moves

    def _piece_moves(self, sq, piece, moves, captures_only):
        color = color_of(piece)
        kind = piece.upper()
        f, r = sq

        def add(target):
            victim = self.get(target)
            if victim is not None and color_of(victim) == color:
                return False  # blocked by own piece
            if captures_only and victim is None:
                return False
            moves.append(Move(sq, target))
            return victim is None  # True if square was empty (keep sliding)

        if kind == "P":
            fwd = 1 if color == WHITE else -1
            start_rank = 1 if color == WHITE else 6
            promo_rank = 7 if color == WHITE else 0
            one = (f, r + fwd)
            if on_board(one) and self.get(one) is None:
                if not captures_only:
                    self._add_pawn_move(moves, sq, one, r + fwd == promo_rank)
                two = (f, r + 2 * fwd)
                if r == start_rank and self.get(two) is None and not captures_only:
                    moves.append(Move(sq, two))
            for df in (-1, 1):
                cap = (f + df, r + fwd)
                if not on_board(cap):
                    continue
                victim = self.get(cap)
                if victim is not None and color_of(victim) != color:
                    self._add_pawn_move(moves, sq, cap, r + fwd == promo_rank)
                elif cap == self.ep_target:
                    moves.append(Move(sq, cap))
        elif kind == "N":
            for df, dr in KNIGHT_DELTAS:
                if on_board((f + df, r + dr)):
                    add((f + df, r + dr))
        elif kind == "K":
            for df, dr in KING_DELTAS:
                if on_board((f + df, r + dr)):
                    add((f + df, r + dr))
            self._castling_moves(sq, color, moves)
        else:
            dirs = (BISHOP_DIRS if kind == "B" else
                    ROOK_DIRS if kind == "R" else KING_DELTAS)
            for df, dr in dirs:
                sf, sr = f + df, r + dr
                while on_board((sf, sr)):
                    if not add((sf, sr)):
                        break
                    sf += df
                    sr += dr

    def _add_pawn_move(self, moves, sq, target, promotes):
        if promotes:
            for promo in "qrbn":
                moves.append(Move(sq, target, promo))
        else:
            moves.append(Move(sq, target))

    def _castling_moves(self, sq, color, moves):
        king = "K" if color == WHITE else "k"
        rook = "R" if color == WHITE else "r"
        rank = 0 if color == WHITE else 7
        enemy = BLACK if color == WHITE else WHITE
        if self.get((4, rank)) != king or self.is_attacked((4, rank), enemy):
            return
        rights = ("K", "Q") if color == WHITE else ("k", "q")
        # kingside: empty f1/g1, rook on h1, f1/g1 not attacked
        if rights[0] in self.castling:
            if (self.get((5, rank)) is None and self.get((6, rank)) is None
                    and self.get((7, rank)) == rook
                    and not self.is_attacked((5, rank), enemy)
                    and not self.is_attacked((6, rank), enemy)):
                moves.append(Move((4, rank), (6, rank)))
        # queenside: empty b1/c1/d1, rook on a1, d1/c1 not attacked
        if rights[1] in self.castling:
            if (self.get((1, rank)) is None and self.get((2, rank)) is None
                    and self.get((3, rank)) is None and self.get((0, rank)) == rook
                    and not self.is_attacked((3, rank), enemy)
                    and not self.is_attacked((2, rank), enemy)):
                moves.append(Move((4, rank), (2, rank)))

    def legal_moves(self):
        """Fully legal moves for the side to move (king safety applied)."""
        result = []
        mover = self.turn
        enemy = BLACK if mover == WHITE else WHITE
        for move in self.pseudo_legal_moves():
            after = self.clone()
            after._apply(move)
            king = after.find_king(mover)
            if king is not None and not after.is_attacked(king, enemy):
                result.append(move)
        return result

    # --------------------------------------------------------- move making

    def make_move(self, move):
        """Apply `move` and update all state. Returns the captured piece (or None)."""
        captured = self._apply(move)
        color = self.turn
        enemy = BLACK if color == WHITE else WHITE
        self.turn = enemy
        if color == BLACK:
            self.fullmove += 1
        return captured

    def _apply(self, move):
        """Move the pieces and update state except turn/fullmove bookkeeping."""
        color = self.turn
        f0, r0 = move.from_sq
        f1, r1 = move.to_sq
        piece = self.get(move.from_sq)
        kind = piece.upper()
        captured = self.get(move.to_sq)

        # en passant capture
        if kind == "P" and move.to_sq == self.ep_target and captured is None:
            captured = self.get((f1, r0))
            self.set((f1, r0), None)

        # castling: move the rook too
        if kind == "K" and abs(f1 - f0) == 2:
            rank = r0
            if f1 == 6:  # kingside
                self.set((5, rank), self.get((7, rank)))
                self.set((7, rank), None)
            elif f1 == 2:  # queenside
                self.set((3, rank), self.get((0, rank)))
                self.set((0, rank), None)

        # castling rights
        if kind == "K":
            self.castling -= {"K", "Q"} if color == WHITE else {"k", "q"}
        for corner, right in (((0, 0), "Q"), ((7, 0), "K"),
                              ((0, 7), "q"), ((7, 7), "k")):
            if (f0, r0) == corner or (f1, r1) == corner:
                self.castling.discard(right)

        # en passant target (set only after a double pawn push)
        if kind == "P" and abs(r1 - r0) == 2:
            self.ep_target = (f0, (r0 + r1) // 2)
        else:
            self.ep_target = None

        # halfmove clock
        if kind == "P" or captured is not None:
            self.halfmove = 0
        else:
            self.halfmove += 1

        self.set(move.from_sq, None)
        self.set(move.to_sq, piece if move.promotion is None else
                 (move.promotion.upper() if color == WHITE else move.promotion))
        return captured

    @staticmethod
    def startpos():
        """A board set up in the standard starting position."""
        return Board.from_fen(START_FEN)
