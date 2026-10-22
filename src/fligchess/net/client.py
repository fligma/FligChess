"""A small https client: the joining app talks to a hosting app with this.

LAN play is two FligChess programs, one serving (net.host.Host in `web=False`
mode) and one connecting here. There is no browser page and no QR code in the
LAN game, but the wire is the very same one a web guest uses - `/api/state`,
`/api/move` and `/api/chat` - so the two apps share one protocol.

The server usually runs on a self-signed certificate no browser trust store
knows, so the client's TLS context accepts any certificate; who to trust is
the app's business, not OpenSSL's. Requests keep cookies, so a login (when
the host set a password) is exchanged once and carried on every call after.
"""

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

from fligchess.net import cert


class Client:
    """One connection to a hosted game, driven from the app's main thread."""

    def __init__(self, url, password="", name=""):
        url = (url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            # a bare "192.168.1.5:8000" means our own https server
            url = "https://" + url
        # a pasted share link can carry "?password=x" (and a trailing slash);
        # the base we talk to is only scheme://host:port, so strip the query
        # and any path - the password belongs to the login, not the address
        parts = urllib.parse.urlsplit(url)
        if parts.scheme and parts.netloc:
            url = "%s://%s" % (parts.scheme, parts.netloc)
        self.url = url.rstrip("/")
        self.password = password or ""
        self.name = name or ""
        jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(jar)]
        if self.url.startswith("https"):
            handlers.append(urllib.request.HTTPSHandler(
                context=cert.client_context()))
        self._op = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------- requests

    def _get(self, path):
        with self._op.open(self.url + path, timeout=5) as resp:
            return json.load(resp)

    def _post(self, path, obj):
        data = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with self._op.open(req, timeout=5) as resp:
            return json.load(resp)

    def login(self):
        """Exchange a password once, if the host asked for one. Returns True
        when the seat is ours to play on."""
        if not self.password:
            return True
        try:
            return bool(self._post("/api/login",
                                   {"password": self.password}).get("ok"))
        except (urllib.error.URLError, ValueError, OSError):
            return False

    def join(self, name=None, color=None):
        """Ask the host for the playing seat, naming ourselves and (optionally)
        the side we would like to play. This client is an app, and says so:
        a cross host only seats it on a side opened for app players. The host
        seats us right away - no waiting for the first poll's auto-seat - and
        carries the side ask to its app, which honours it while the lobby
        still waits. Returns the server's reply: {"ok": True, "role": ...},
        or {"ok": False} when every matching seat is taken and no spectating
        is allowed."""
        body = {}
        if name:
            body["name"] = name
        if color:
            body["color"] = color
        try:
            return self._post("/api/join", dict(body, kind="app"))
        except (urllib.error.URLError, ValueError, OSError):
            return {"ok": False, "spectate": False}

    def state(self, since=0):
        """The position as the server sees it, plus chat newer than `since`."""
        return self._get("/api/state?since=%d" % since)

    def move(self, from_sq, to_sq, promotion=None):
        return self._post("/api/move", {"from": from_sq, "to": to_sq,
                                        "promotion": promotion})

    def chat(self, text):
        return self._post("/api/chat", {"text": text, "name": self.name})

    def leave(self):
        """Tell the host we are done: our seat is freed at once so another
        player can take it without waiting for our polls to lapse."""
        try:
            return self._post("/api/leave", {})
        except (urllib.error.URLError, ValueError, OSError):
            return {"ok": False}
