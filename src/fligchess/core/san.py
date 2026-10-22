"""Standard Algebraic Notation (SAN) for moves, like chess.com shows them."""


def _square_name(sq):
    return "abcdefgh"[sq[0]] + str(sq[1] + 1)


def san(board, move):
    """Notate `move` on `board` (before the move is made), e.g. 'Nbd2xg7+'."""
    piece = board.get(move.from_sq)
    kind = piece.upper()
    f0, r0 = move.from_sq
    f1, r1 = move.to_sq

    # castling
    if kind == "K" and abs(f1 - f0) == 2:
        base = "O-O" if f1 == 6 else "O-O-O"
        return _with_check(board, move, base)

    text = ""
    if kind != "P":
        text += kind
        rivals = [m for m in board.legal_moves()
                  if m != move and m.to_sq == move.to_sq
                  and board.get(m.from_sq) == piece]
        if rivals:
            same_file = any(m.from_sq[0] == f0 for m in rivals)
            same_rank = any(m.from_sq[1] == r0 for m in rivals)
            if not same_file:
                text += "abcdefgh"[f0]
            elif not same_rank:
                text += str(r0 + 1)
            else:
                text += _square_name(move.from_sq)

    captured = board.get(move.to_sq)
    if captured is not None or (kind == "P" and move.to_sq == board.ep_target):
        if kind == "P":
            text += "abcdefgh"[f0]
        text += "x"
    text += _square_name(move.to_sq)

    if move.promotion:
        text += "=" + move.promotion.upper()
    return _with_check(board, move, text)


def _with_check(board, move, text):
    after = board.clone()
    after.make_move(move)
    if after.in_check():
        text += "#" if not after.legal_moves() else "+"
    return text
