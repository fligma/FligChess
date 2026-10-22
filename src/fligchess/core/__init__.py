"""Core chess rules engine for FligChess.

This module is also the public, importable API for bot scripts living in
the `bots/` folder: `Bot`, `Position` and `Move` are what they should use.
"""

from fligchess.core.board import Board, Move, WHITE, BLACK
from fligchess.core.game import ChessGame, WHITE_WINS, BLACK_WINS, DRAW
from fligchess.core.san import san
from fligchess.core import variants
from fligchess.core.bot_api import (Bot, Position, GameInfo, mode_matches,
                                    bot_allows, load_bots)
from fligchess.core.paths import bots_dir, config_dir, APP_ROOT

__all__ = ["Board", "Move", "WHITE", "BLACK", "ChessGame", "san",
           "WHITE_WINS", "BLACK_WINS", "DRAW", "variants",
           "Bot", "Position", "GameInfo", "mode_matches", "bot_allows",
           "load_bots", "bots_dir", "config_dir", "APP_ROOT"]
