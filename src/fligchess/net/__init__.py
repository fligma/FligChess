"""Hosting a game for someone else to play against, over HTTP.

`host` runs the small server a guest's browser talks to, `page` is the
XP-styled page that server ships, and `qr` draws the picture a phone scans
to open it - all of it free of pygame and of any third-party package, so a
plain install can host a game.
"""

from fligchess.net import page, qr
from fligchess.net.host import Host, free_port, local_ip

__all__ = ["Host", "free_port", "local_ip", "page", "qr"]
