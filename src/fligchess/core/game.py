"""Game-level state: turns, results and draw detection."""

from fligchess.core.board import Board, WHITE
from fligchess.core.board4 import Board4, ARMIES as _ARMIES4

WHITE_WINS = "white wins"
BLACK_WINS = "black wins"
DRAW = "draw"


class ChessGame:
    """Wraps a Board with move history and game-over detection."""

    def __init__(self, board=None):
        self.board = board or Board.startpos()
        self.moves = []          # moves played this game
        self.repetitions = {}    # fen -> count
        self.resigned = None     # color that resigned, ends the game
        self._record_position()

    # ------------------------------------------------------------- queries

    def legal_moves(self):
        return self.board.legal_moves()

    def moves_from(self, sq):
        """Legal moves originating on a given square."""
        return [m for m in self.board.legal_moves() if m.from_sq == sq]

    def status_text(self):
        """One-line description of the current position, e.g. 'White to move'."""
        result = self.result()
        if result:
            return self._result_text(result)
        check = " check" if self.board.in_check() else ""
        side = "White" if self.board.turn == WHITE else "Black"
        return f"{side} to move{check}"

    def result(self):
        """None while the game runs, otherwise WHITE_WINS / BLACK_WINS / DRAW."""
        if self.resigned:
            return BLACK_WINS if self.resigned == WHITE else WHITE_WINS
        if not self.board.legal_moves():
            if self.board.in_check():
                return BLACK_WINS if self.board.turn == WHITE else WHITE_WINS
            return DRAW  # stalemate
        if self.board.halfmove >= 100:
            return DRAW  # fifty-move rule
        if self.repetitions.get(self._position_key(), 0) >= 3:
            return DRAW  # threefold repetition
        if self._insufficient_material():
            return DRAW
        return None

    def _result_text(self, result):
        if self.resigned:
            loser = "White" if self.resigned == WHITE else "Black"
            winner = "black" if self.resigned == WHITE else "white"
            return f"{loser} resigns - {winner} wins!"
        if result == DRAW:
            if not self.board.legal_moves():
                return "Draw by stalemate"
            if self.board.halfmove >= 100:
                return "Draw by fifty-move rule"
            if self.repetitions.get(self._position_key(), 0) >= 3:
                return "Draw by threefold repetition"
            return "Draw by insufficient material"
        return f"Checkmate - {result}!"

    def _insufficient_material(self):
        """Draw only when no mate is possible: K vs K, or K+minor vs K.
        A queen or rook (or two minors, or pawns) can still mate."""
        pieces = [p for row in self.board.squares for p in row if p]
        if any(p.upper() == "P" for p in pieces):
            return False
        non_kings = [p for p in pieces if p.upper() not in "KP"]
        if not non_kings:
            return True  # K vs K
        return len(non_kings) == 1 and non_kings[0].upper() in "BN"

    # ---------------------------------------------------------- move playing

    def play_move(self, move):
        """Play a legal move and return it. Raises ValueError if illegal."""
        if move not in self.board.legal_moves():
            raise ValueError(f"Illegal move: {move}")
        self.board.make_move(move)
        self.moves.append(move)
        self._record_position()
        return move

    def _position_key(self):
        """Pieces + turn + castling + en passant (ignores move counters)."""
        parts = self.board.to_fen().split()
        return " ".join(parts[:4])

    def _record_position(self):
        key = self._position_key()
        self.repetitions[key] = self.repetitions.get(key, 0) + 1

    # ------------------------------------------------------------- utilities

    def undo(self, reapply=None):
        """Rebuild the game one move back (simple, no unmake needed).

        `reapply(board, move)` runs straight after each replayed move, so a
        variant that edits the board as it goes - a mine killing whatever trod
        it - is redone in step with the moves. Without it the rebuilt position
        keeps pieces that were blown up, and the moves after that point stop
        being legal.
        """
        if not self.moves:
            return False
        history = [m for m in self.moves[:-1]]
        game = ChessGame(Board.startpos())
        for move in history:
            game.play_move(move)
            if reapply is not None:
                reapply(game.board, move)
        self.__dict__.update(game.__dict__)
        return True


# Names shown for each army in the four-player game's status and result text.
ARMY_NAMES = {"w": "White", "b": "Black", "r": "Red", "bl": "Blue"}

# A cross game ends only on elimination, so evenly-matched armies can shuffle
# forever. Two safeguards break a stall; both are recomputed from the move
# list alone, so an undo replays to exactly the same state.
DRAW4 = "draw"          # result sentinel when the absolute cap was reached
REP_CAP = 4             # position offered to a mover this many times -> it loses
MAX_PLIES4 = 1500       # last-resort ceiling so a 4P game always terminates
RECENT_WINDOW = 24      # last offered positions a cross engine is warned about


def _sig4(board):
    """The repetition signature of a cross position: pieces + turn + en
    passant, the move counters dropped. The bot API reads the same first four
    FEN fields, so a classic engine's own view and the game's counts agree."""
    return " ".join(board.to_fen().split()[:4])


class Game4:
    """A free-for-all on the cross board: four armies, turn order white,
    black, red, blue. An army that cannot move on its turn (checkmated or
    stalemated) - or whose king is captured - is eliminated and its pieces
    come off; the last army with a king standing wins.

    It keeps a Board4 plus the move list, mirroring ChessGame's surface so
    the app can drive either through the same calls."""

    def __init__(self, board=None):
        self.board = board or Board4.startpos()
        self.moves = []            # every move played, across all armies
        self.eliminated = []       # (army, reason) in the order they fell
        self.resigned = None       # army that resigned, ends for it
        self.repetitions = {}      # position signature -> times offered to a mover
        self.recent = []           # last offered positions, newest last
        self.capped = False        # hit MAX_PLIES4 while two-plus armies shuffled
        self._advance()

    # ------------------------------------------------------------- queries

    def legal_moves(self, army=None):
        return self.board.legal_moves(army)

    def moves_from(self, sq):
        return [m for m in self.board.legal_moves() if m.from_sq == sq]

    def standing(self):
        """Armies still in the game: not eliminated and still owning a king."""
        return [a for a in _ARMIES4
                if a not in self.board.out and self.board.has_king(a)]

    def result(self):
        """None while the game runs; otherwise the winner's army id, or the
        DRAW4 sentinel when the absolute progress cap was reached."""
        if self.capped:
            return DRAW4
        if self.resigned:
            # the resigner is gone; the last of the others wins outright
            alive = [a for a in self.standing() if a != self.resigned]
            return alive[0] if len(alive) == 1 else None
        alive = self.standing()
        return alive[0] if len(alive) <= 1 else None

    def status_text(self):
        winner = self.result()
        if winner == DRAW4:
            return "Draw - no progress"
        if winner:
            return "%s wins - last king standing" % ARMY_NAMES[winner]
        turn = self.board.turn
        check = " - check!" if self.board.in_check(turn) else ""
        return "%s to move%s" % (ARMY_NAMES.get(turn, "?"), check)

    # ---------------------------------------------------------- move playing

    def play_move(self, move):
        if move not in self.board.legal_moves():
            raise ValueError("Illegal move: %s" % (move,))
        self.board.make_move(move)
        self.moves.append(move)
        self._advance()
        return move

    # ------------------------------------------------- repetition and stalling

    def _position_key4(self):
        """Pieces + turn + en passant, counters ignored: the exact state a
        mover is offered, so a shuffle shows up as the same key recurring."""
        return _sig4(self.board)

    def _record_position(self):
        key = self._position_key4()
        self.repetitions[key] = self.repetitions.get(key, 0) + 1
        self.recent.append(key)
        if len(self.recent) > RECENT_WINDOW:
            del self.recent[:-RECENT_WINDOW]

    def _advance(self):
        """Settle the game after a move: sweep eliminations, count the
        position now offered to the mover, and evict that mover once it is
        the REP_CAP-th time round for the same position. The armies that have
        not been looping stay in play; only a last-standing (or capped) state
        ends the game. The whole thing is a pure function of the move list."""
        self._resolve()
        if self.result() is not None:
            return
        self._record_position()
        turn = self.board.turn
        if (turn is not None and len(self.standing()) > 1
                and self.repetitions.get(self._position_key4(), 0)
                >= REP_CAP):
            self.board.remove_army(turn)
            self.eliminated.append((turn, "repetition"))
            self._resolve()
        if (self.result() is None and not self.capped
                and len(self.moves) >= MAX_PLIES4):
            self.capped = True

    def resign(self, army):
        """An army drops out; everyone else keeps playing to last-standing."""
        if army in self.board.out:
            return
        self.board.remove_army(army)
        self.eliminated.append((army, "resigned"))
        self.resigned = army

    def _resolve(self):
        """After any move, clear out armies that can no longer act.

        An army missing its king has been captured outright; the army whose
        turn it is with no legal move is checkmated (if in check) or stalemated.
        Both are stripped from the board, which may open the next army's turn,
        so the sweep repeats until somebody can move or the game is decided."""
        while True:
            if self.result() is not None:
                return
            for a in _ARMIES4:
                if a not in self.board.out and not self.board.has_king(a):
                    self.board.remove_army(a)
                    self.eliminated.append((a, "captured"))
            turn = self.board.turn
            if turn is None or self.board.legal_moves(turn):
                return
            reason = "checkmate" if self.board.in_check(turn) else "stalemate"
            self.board.remove_army(turn)
            self.eliminated.append((turn, reason))

    # ------------------------------------------------------------- utilities

    def undo(self, reapply=None):
        """Rebuild one ply back by replaying from the start position, so the
        elimination sweep runs again in exactly the same order."""
        if not self.moves:
            return False
        history = list(self.moves[:-1])
        game = Game4()
        for move in history:
            game.play_move(move)
            if reapply is not None:
                reapply(game.board, move)
        self.__dict__.update(game.__dict__)
        return True
