"""Standard Algebraic Notation for the four-player cross board.

Like ``san`` for the classic game, but the pieces live on a 14-wide board
(files a-n), there is no castling, and a move is tagged with the army that
made it so a single history of four interleaved armies stays readable. A
disambiguation among an army's own pieces mirrors the two-player rules: add
the file, else the rank, else the whole square, when two same-kind pieces of
that army can reach the target.
"""

from fligchess.core.board4 import kind_of, army_of, sq_name, ARMIES

FILES = "abcdefghijklmn"


def san4(board, move):
    """Notate `move` on `board` before it is made. Returns e.g. "r:Nf-c3".

    The leading army id names whose turn it is; the rest is ordinary SAN."""
    code = board.get(move.from_sq)
    army = army_of(code)
    kind = kind_of(code)
    text = kind if kind != "P" else ""

    if kind != "P":
        rivals = [m for m in board.legal_moves(army)
                  if m != move and m.to_sq == move.to_sq
                  and kind_of(board.get(m.from_sq)) == kind]
        if rivals:
            f0, r0 = move.from_sq
            same_file = any(m.from_sq[0] == f0 for m in rivals)
            same_rank = any(m.from_sq[1] == r0 for m in rivals)
            if not same_file:
                text += FILES[f0]
            elif not same_rank:
                text += str(r0 + 1)
            else:
                text += sq_name(move.from_sq)

    victim = board.get(move.to_sq)
    if victim is not None or (kind == "P" and board.ep
                              and move.to_sq == board.ep[0]):
        if kind == "P":
            text += FILES[move.from_sq[0]]
        text += "x"
    text += sq_name(move.to_sq)
    if move.promotion:
        text += "=" + move.promotion.upper()
    return "%s:%s%s" % (army, text, _ticks(board, move, army))


def _ticks(board, move, army):
    """'+' if the move checks a standing enemy king, '#' if it mates one."""
    after = board.clone()
    after.make_move(move)
    mate = check = False
    for other in after.alive_armies():
        if other == army:
            continue
        if after.in_check(other):
            check = True
            if not after.legal_moves(other):
                mate = True
    return "#" if mate else "+" if check else ""
