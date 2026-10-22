"""Hosting a game so someone else can play it from a browser.

Everything here is deliberately free of pygame and of the app: a `Host`
owns the one bit of shared state a running game needs - the position to
show the guest, and a queue of moves the guest has asked to make - and
serves them over a small HTTP server on a background thread. The app
publishes a snapshot each frame and drains the move queue, so the rules
stay in the app's single thread and the server only ever shuffles JSON.

The wire contract is tiny:

    GET  /            the play page (XP-styled HTML, polls the two below)
    GET  /api/state   the position as JSON, seen through the guest's eyes
    POST /api/move    {"from": "e2", "to": "e4", "promotion": "q"}

A password, when set, is exchanged once through a login page and then
carried in a cookie. Spectating can be switched off, in which case only
the seated guest is ever handed the board.
"""
import http.cookies
import json
import pathlib
import socket
import threading
import time
import urllib.parse
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fligchess.net import page

# the bundled artwork a browser page needs; resolved off this package so the
# server stays free of the app and of pygame, reading the files as raw bytes
_ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets"

# a browser asks for this on its own; the app's logo answers it
FAVICON = "/favicon.ico"
# the piece sprite sheet the page draws its board from (a 6x2 grid)
PIECES = "/assets/pieces.png"
# the second sheet: the cross game's red and blue pallets (also 6x2)
PIECES2 = "/assets/pieces2.png"
AUTH = "auth"              # password OK (or none asked) but not yet seated
GUEST = "guest"            # the token the seated player plays under
VIEWER = "viewer"          # the token a spectator watches under

# A playing seat is bound to one connection, not to a shared cookie. If the
# holder stops polling (closes the tab, drops off the network) for this many
# seconds the seat is considered abandoned and freed, so a waiting spectator
# can take it over on their next poll or Join. HTTP has no close event, so a
# lapsed poll is the only signal we get.

# A four-player host opens several seats at once - one per army the app says
# is a human's - and each is bound to its own session the same way: it lists
# which client kind may take it ("web" or "app", or "any"), which army it
# holds, and whose polls keep it alive. Two-player hosting is the same model
# with a single seat that matches any kind and either colour.
SEAT_TIMEOUT = 15.0


def _new_sid():
    """A short id naming one browser session, carried in its role cookie so
    the seat can be bound to the connection that claimed it."""
    return uuid.uuid4().hex[:12]


def free_port():
    """An unused TCP port, picked by the OS, for the default in the setup
    page. The caller still binds it, so treat it as a hint, not a promise."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def local_ip():
    """The address other machines on this network can reach us on. Opening a
    datagram socket to a far-off address never sends a packet; it only makes
    the OS choose the interface that route would use, whose address is ours.
    Falls back to loopback when there is no network at all."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Host:
    """The shared state and the server thread in front of it.

    Build one, `start()` it, then every frame call `publish(snapshot)` with
    the position to show and take `take_move()` whenever the guest is due to
    play. `url` is what the QR code and the setup dialog display.
    """

    def __init__(self, port=0, password="", spectate=True, guest_name="Guest",
                 host_name="Host", tls=None, web=True, lock_after_join=True,
                 four=False, host_army="w", seats=None):
        self.password = password or ""
        self.spectate = spectate
        self.lock_after_join = lock_after_join   # keep the gate up once seated
        self.guest_name = guest_name
        self.host_name = host_name
        self.tls = tls                  # a server ssl.SSLContext, or None
        self.web = web                  # serve the browser page, or JSON only
        # the shape of the hosted game, so the lobby can offer the open
        # sides before anyone is seated (the app owns them and keeps them
        # current with set_shape when a player's pick moves the host)
        self.four = four
        self.host_army = host_army
        # the playing seats, in the order the app opened them. Each holds:
        #   color: the army it keeps (None: any side, plain two-player)
        #   kind:  which client may take it - "web", "app" or "any"
        #   sid:   the session holding it now (None while free)
        #   name:  what its holder called themselves
        #   seen:  when the holder last polled
        # Two-player hosting is one {color: None, kind: "any"} seat, so the
        # old single-seat semantics - and the swap-the-host picks - survive.
        if seats is not None:
            self._slots = [{"color": s.get("color"),
                            "kind": s.get("kind") or "any",
                            "sid": None, "name": "", "seen": 0.0}
                           for s in seats]
        else:
            self._slots = [{"color": None, "kind": "any", "sid": None,
                            "name": "", "seen": 0.0}]
        self._lock = threading.Lock()
        self._snapshot = {"status": "waiting", "moves": [], "pieces": {},
                          "turn": "w", "guest_color": "w", "dests": {}}
        self._moves = deque()          # guest moves waiting for the app
        self._resign = deque()         # resignations, tagged with their army
        self._chat = []                # the shared chat log, oldest first
        self._chat_pending = deque()   # guest chat waiting for the app
        self._chat_seq = 0             # id handed to the next chat message
        # what each seat holder called themselves lives on their slot, so a
        # reconnect (a new sid claiming a freed seat) is prompted again rather
        # than inheriting the old player's name. The host never sets it: a
        # player names themselves on joining.
        self._seat_names = {}
        # the side and name a joining or seated player has asked for, waiting
        # for the app to act on it (one slot: the latest ask wins; the app
        # drains it with take_picks() while the lobby waits, and ignores it
        # once Play has started). None means nothing is outstanding.
        self._pick = None
        self._visitors = 0
        # per-session traffic counters for the "View server stats" panel,
        # keyed by the same session id the seat is bound to; filled in by the
        # request handler (see note_activity/note_rtt). Plain HTTP gives no
        # ICMP, so RTT is the approximate poll round-trip a client echoes back
        # and "loss" is the share of requests that errored out.
        self._stats = {}
        self._start_time = time.time()
        # chat names claimed this session, lower-cased: a spectator must
        # register one before they can talk, and no two may share it. The
        # seated player's and host's names live here too so a spectator
        # cannot impersonate them.
        self._names = {self.host_name.lower(), self.guest_name.lower()}
        self._server = None
        self._thread = None
        self._want_port = port

    # ------------------------------------------------------------- lifecycle

    def start(self):
        """Bind and serve on a daemon thread; returns the live URL."""
        self._start_time = time.time()
        host, port = "0.0.0.0", self._want_port or free_port()
        self._server = ThreadingHTTPServer((host, port), _make_handler(self))
        self._server.daemon_threads = True
        if self.tls is not None:
            # wrap the listening socket so every connection is HTTPS
            self._server.socket = self.tls.wrap_socket(
                self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        return self.url

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self):
        scheme = "https" if self.tls is not None else "http"
        return f"{scheme}://{local_ip()}:{getattr(self, 'port', self._want_port)}"

    @property
    def share_url(self):
        """The link to hand a guest: the bare address with the password folded
        in as a query, so opening it logs them straight in (they still press
        Join to take a seat). The bare `url` stays for a LAN peer's address."""
        if not self.password:
            return self.url
        return self.url + "/?password=" + urllib.parse.quote(self.password)

    # ------------------------------------------------------------ app-facing

    def publish(self, snapshot):
        """Hand the server the position to show, as a plain dict."""
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self):
        """The current position, copied out from behind the lock."""
        with self._lock:
            return dict(self._snapshot)

    def take_move(self, color=None):
        """The next guest move as a dict, or None if nothing is waiting. With
        a `color`, only a move queued by the seat holding that army counts,
        and it comes off the queue wherever it sits: the others wait their
        own army's turn, so one slow player cannot block the rest."""
        with self._lock:
            if color is None:
                return self._moves.popleft() if self._moves else None
            for i, move in enumerate(self._moves):
                if move.get("color") in (None, color):
                    del self._moves[i]
                    return move
            return None

    def submit_resign(self, token, sid=None):
        """A seated player has asked to resign; the app picks it up once, and
        in a multi-seat game learns which army gave up."""
        if token != GUEST:
            return False
        with self._lock:
            slot = self._seat_of(sid)
            self._resign.append((slot or {}).get("color", ""))
        return True

    def take_resign(self):
        """The army of the next waiting resignation once ("" for a plain
        two-player seat whose army the app already knows), or None when the
        queue is empty - so `if host.take_resign() is not None:` still works."""
        with self._lock:
            return self._resign.popleft() if self._resign else None

    def offer_seat(self):
        """Claim a playing seat for whoever just connected - the first browser
        to arrive plays, later ones can only watch, and watching is off unless
        the host allowed it.
        A cross game seats the peer on its first free seat, whatever side that
        is - this is the host app's own call for a LAN peer, not a browser
        naming a client kind, so it is not filtered by seat kind. The colour
        each side plays is decided by the app and carried in the snapshot,
        never here. The LAN auto-seat path uses this; a web browser asks for
        the seat explicitly via join().
        """
        return self.join()

    def join(self, sid=None, name=None, color=None, kind=None):
        """Claim a role when a visitor presses Join: a free seat the visitor's
        client kind (and asked-for army, when they named one) matches, or the
        lone two-player seat; otherwise spectate if the host allows it,
        otherwise nothing at all. Only ever reached from an explicit Join, so
        a browser is never seated by merely loading the page. Each seat is
        remembered as belonging to `sid`, so only that one connection can act
        as its player; a direct caller who passes no id gets a fresh one minted
        for it. The seated player may name themselves (`name`) and ask for a
        side (`color`: any army of the cross in a four-player game): the name
        is stored on their seat, never inherited by whoever claims it later,
        and the ask goes to the app."""
        with self._lock:
            self._release_expired()
            sid = sid or _new_sid()
            slot = self._match_slot(sid, color, kind)
            if slot is not None:
                slot["sid"] = sid
                slot["seen"] = time.time()
                chosen = (name or "").strip()[:12]
                if chosen:
                    slot["name"] = chosen
                    self._seat_names[sid] = chosen
                if color or chosen:
                    self._pick = {"color": color, "name": chosen or None,
                                  "sid": sid}
                return GUEST
            if self.spectate:
                return VIEWER
            return None

    def _match_slot(self, sid, color=None, kind=None):
        """The free seat this joiner may take, lock held: one they hold
        already, else an unfilled slot whose kind fits the client. An ask for
        a side is a preference, not a gate: the seat holding it is taken first
        if it is free, and when it is not the joiner lands on any other seat
        their kind may hold (so a stale side pick never turns a player away).
        A two-player game's lone seat fits any kind and any colour, as it
        always did, and a caller naming no kind at all - the host app seating a
        LAN peer itself - fits any seat too."""
        for slot in self._slots:
            if slot["sid"] == sid:
                return slot
        want = (kind or "").lower()
        free = [s for s in self._slots
                if s["sid"] is None and (not want or s["kind"] in ("any", want))]
        if color:
            for slot in free:
                if slot["color"] in (None, color):
                    return slot
        return free[0] if free else None

    def seats(self):
        """A copy of every seat for the app and the lobby page: the army it
        holds, the client kind it accepts, whether it is taken and the name
        its holder gave. Lapsed holders are released first, so what is shown
        is what a newcomer could actually take. The holder's session id is
        deliberately left out - it binds the seat, so publishing it would
        let another browser claim to be the player."""
        with self._lock:
            self._release_expired()
            return [{"color": s["color"], "kind": s["kind"],
                     "taken": s["sid"] is not None, "name": s["name"]}
                    for s in self._slots]

    def note_pick(self, sid, color=None, name=None):
        """A seated player asks to change side and/or name before the game
        starts; only a session holding a seat may ask. The app picks the ask
        up with take_picks() and applies it while the lobby waits."""
        with self._lock:
            slot = self._seat_of(sid)
            if slot is None:
                return False
            self._pick = {"color": color,
                          "name": (name or "").strip()[:12] or None,
                          "sid": sid}
            return True

    def take_pick(self):
        """The outstanding side/name ask, once: returns the dict and clears
        the slot, or None when nothing is waiting for the app."""
        with self._lock:
            out, self._pick = self._pick, None
        return out

    def take_picks(self):
        """Every outstanding side/name ask drained in order. Each is
        {"color", "name", "sid"}; with the one-slot-per-lobby model this is
        either empty or the lone ask take_pick() would have returned."""
        with self._lock:
            out = [self._pick] if self._pick else []
            self._pick = None
            return out

    def set_shape(self, four, host_army, seats=None):
        """Keep the server's idea of the game's shape in step with the app
        (a two-player pick that moves the host changes which side is taken);
        `seats`, when given, re-arms the open seats too."""
        with self._lock:
            self.four = bool(four)
            self.host_army = host_army
            if seats:
                for slot, s in zip(self._slots, seats):
                    slot["color"] = s.get("color")
                    slot["kind"] = s.get("kind") or "any"

    def reseat(self, sid, color):
        """Move a seated player onto the seat holding the army they asked
        for, when that seat is free. The seat's client kind is respected, so
        a browser cannot hop onto a side opened for apps and use up its slot.
        True when this session now holds the wanted seat."""
        with self._lock:
            cur = self._seat_of(sid)
            if cur is None:
                return False
            if cur["color"] == color:
                return True
            target = next((s for s in self._slots
                           if s["sid"] is None and s["color"] == color
                           and s["kind"] in ("any", cur["kind"])), None)
            if target is None:
                return False
            target["sid"], target["name"], target["seen"] = (
                sid, cur["name"], time.time())
            cur["sid"], cur["name"], cur["seen"] = None, "", 0.0
            if target["name"]:
                self._seat_names[sid] = target["name"]
            else:
                self._seat_names.pop(sid, None)
            return True

    def seat_colors(self, kind=None):
        """The sides a joining player may ask for: either colour of a
        two-player game (the host simply takes the other), and in a
        four-player game the army of every seat still free and opened for
        this kind of client."""
        with self._lock:
            self._release_expired()
            if not self.four:
                return ["w", "b"]
            return [s["color"] for s in self._free_slots(kind) if s["color"]]

    def _free_slots(self, kind=None):
        """Every seat a client of this kind could still take, lock held: one
        nobody holds and whose kind fits (`kind` left out means every free
        seat, whatever client it was opened for)."""
        want = (kind or "").lower()
        return [s for s in self._slots
                if s["sid"] is None
                and (not want or s["kind"] in ("any", want))]

    def seat_open(self, kind=None):
        """Whether a client of this kind could still be seated to play. With
        several seats one being taken does not shut the lobby on the next
        browser: the door stays open until every matching seat is held."""
        with self._lock:
            self._release_expired()
            return bool(self._free_slots(kind))

    def _release_expired(self):
        """Free every seat whose holder has stopped polling (called with the
        lock held); the slot goes back to unfilled so a newcomer - or the
        same player, reloading - can claim it again."""
        now = time.time()
        for slot in self._slots:
            if (slot["sid"] is not None and slot["seen"]
                    and now - slot["seen"] > SEAT_TIMEOUT):
                self._seat_names.pop(slot["sid"], None)
                slot["sid"] = None
                slot["name"] = ""
                slot["seen"] = 0.0

    def _seat_of(self, sid):
        """The seat this session holds, or None; lock held by the caller."""
        if sid is None:
            return None
        for slot in self._slots:
            if slot["sid"] == sid:
                return slot
        return None

    def _first_seat(self):
        """The first held seat, lock held: the one to read for a caller that
        names no session (a direct app-side call in a two-player game)."""
        for slot in self._slots:
            if slot["sid"]:
                return slot
        return None

    def seat_of(self, sid):
        """A copy of the seat this session holds, for the app and the state
        route: its army and name, or None when it plays no seat."""
        with self._lock:
            slot = self._seat_of(sid)
            if slot is None:
                return None
            return {"color": slot["color"], "kind": slot["kind"],
                    "name": slot["name"], "taken": True}

    def drop_seat(self, sid):
        """Free the seat this session holds: a client that leaves cleanly (an
        app closing its game) reopens the slot at once instead of waiting
        for its polls to lapse, so someone else can take it straight away."""
        with self._lock:
            slot = self._seat_of(sid)
            if slot is None:
                return False
            self._seat_names.pop(sid, None)
            slot["sid"] = None
            slot["name"] = ""
            slot["seen"] = 0.0
            return True

    def has_seat(self, sid):
        """Whether this session id is one holding a playing seat."""
        with self._lock:
            return self._seat_of(sid) is not None

    def touch_seat(self, sid):
        """A seat-holder polled: note the time so their seat is not treated
        as abandoned until SEAT_TIMEOUT of silence."""
        with self._lock:
            slot = self._seat_of(sid)
            if slot is not None:
                slot["seen"] = time.time()

    def try_claim(self, sid, name=None):
        """Give a free seat to this session if one has come open (never
        claimed, or its holder's polls have lapsed); the seat's army when
        the game runs one per side; True when this session now holds a seat.
        A waiting spectator uses this to take over a two-player game left
        unattended; a cross game's seats are only ever taken by an explicit
        Join that names the side it wants. The new holder is not handed the
        old player's name: unless they supply one now, they are prompted for
        it before their first move."""
        with self._lock:
            if self.four:
                return False
            self._release_expired()
            for slot in self._slots:
                if slot["sid"] is None and slot["kind"] in ("any", "web"):
                    slot["sid"] = sid
                    slot["seen"] = time.time()
                    chosen = (name or "").strip()[:12]
                    if chosen:
                        slot["name"] = chosen
                        self._seat_names[sid] = chosen
                    return True
            return False

    def seat_name(self, sid=None):
        """The name a seat holder chose for themselves, or "" while none has
        been given. Without a `sid` this reads the first named seat."""
        with self._lock:
            if sid is not None:
                slot = self._seat_of(sid)
                return slot["name"] if slot else ""
            for slot in self._slots:
                if slot["sid"] and slot["name"]:
                    return slot["name"]
            return ""

    def set_seat_name(self, sid, name):
        """Record (or correct) a seated player's own name against the seat
        their session holds, so a player promoted by taking over a lapsed
        seat can name themselves after the fact. Returns the stored name."""
        chosen = (name or "").strip()[:12]
        with self._lock:
            slot = self._seat_of(sid)
            if slot is not None and chosen:
                slot["name"] = chosen
                self._seat_names[sid] = chosen
        return chosen

    def submit_move(self, token, move, sid=None):
        """Queue a move a seated player asked for, tagged with their seat's
        army so a cross game applies it only on that army's turn."""
        if token != GUEST:
            return False
        with self._lock:
            slot = self._seat_of(sid)
            if slot is not None and slot["color"]:
                move = dict(move, color=slot["color"])
            self._moves.append(move)
        return True

    def note_visitor(self):
        with self._lock:
            self._visitors += 1

    def visitors(self):
        """How many browsers have connected to claim a seat so far."""
        with self._lock:
            return self._visitors

    # -------------------------------------------------------------- chat

    def post_chat(self, who, text, color=""):
        """Append a message to the shared log and return it. Anyone - the
        host app or a guest - adds chat through here so both sides read the
        same ordered history from the snapshot. `color` is the army the sender
        plays ("w"/"b", and "r"/"bl" in a four-player game), so each line can
        be marked with that side's pawn; "" means no side (a spectator)."""
        text = (text or "").strip()
        if not text:
            return None
        with self._lock:
            self._chat_seq += 1
            message = {"seq": self._chat_seq, "who": str(who or "?")[:24],
                       "text": text[:400], "color": (color or "")[:3]}
            self._chat.append(message)
            if len(self._chat) > 200:      # keep the log bounded
                del self._chat[:-200]
            return message

    def submit_chat(self, token, text, name=None, color=None, sid=None):
        """A guest sent a chat line: log it and queue it for the app to show.
        A seated player speaks under the name they chose for themselves (the
        name on the seat their session holds; a LAN peer names in their join
        form), falling back to the placeholder the app supplied. A spectator
        only gets to talk once they have registered a name that is not
        somebody else's - until then this reports need_name so the page can
        prompt for one. The line is marked with the sender's side: a seated
        player takes the army of the seat they hold, a spectator carries
        none. Returns a small status dict: {"ok": True}, {"ok": False,
        "need_name": True}, or {"ok": False} for a browser with no
        recognised role."""
        if token not in (GUEST, VIEWER):
            return {"ok": False}
        if token == VIEWER:
            who = (name or "").strip()[:12]
            if not who or not self.name_taken(who):
                return {"ok": False, "need_name": True}
            side = ""
        else:
            with self._lock:
                # with no session named (a direct app-side call) the lone
                # seat stands; an http request always arrives with its own sid
                slot = self._seat_of(sid) or (self._first_seat()
                                              if sid is None else None) or {}
            who = slot.get("name") or name or self.guest_name
            # a seated player's line carries their seat's army; the plain
            # two-player seat names no army, so the caller supplies the
            # colour the app published for the guest
            side = color or slot.get("color") or \
                self.snapshot().get("guest_color", "")
        message = self.post_chat(who, text, color=side)
        if message is not None:
            with self._lock:
                self._chat_pending.append(message)
        return {"ok": True}

    def name_taken(self, name):
        """Whether this exact chat name has been registered this session
        (compared case-insensitively)."""
        with self._lock:
            return (name or "").strip().lower() in self._names

    def register_name(self, name):
        """Claim a spectator's chat name. Returns (ok, value): on success the
        value is the accepted (trimmed) name, on failure it is the reason -
        "invalid" for a blank one or "taken" when somebody holds it already.
        The seated and host names are reserved up front, so neither can be
        claimed here."""
        cleaned = (name or "").strip()[:12]
        if not cleaned:
            return False, "invalid"
        key = cleaned.lower()
        with self._lock:
            if key in self._names:
                return False, "taken"
            self._names.add(key)
        return True, cleaned

    def take_chat(self):
        """Guest chat the app has not shown yet, drained in order."""
        with self._lock:
            out = list(self._chat_pending)
            self._chat_pending.clear()
            return out

    def chat(self, since=0):
        """The chat log, only messages newer than the `since` sequence."""
        with self._lock:
            return [m for m in self._chat if m["seq"] > since]

    def has_guest(self):
        """Whether at least one playing seat is held right now - the app
        shows a "waiting for a player" message until one is. It reads the
        bound session ids, so a seat whose holder lapsed counts as free."""
        with self._lock:
            self._release_expired()
            return any(s["sid"] is not None for s in self._slots)

    def seat_free(self):
        """Whether some seat is still unclaimed: in a multi-seat game Play
        waits until none is, in a two-player game this is the old latch of
        "the lone seat is still open"."""
        with self._lock:
            self._release_expired()
            return any(s["sid"] is None for s in self._slots)

    def viewer_count(self, window=10.0):
        """How many spectators are watching right now: sessions whose cookie
        says viewer and whose polls landed within the last `window` seconds.
        A browser polls every 600 ms, so a quiet one has simply left."""
        with self._lock:
            now = time.time()
            return sum(1 for b in self._stats.values()
                       if b.get("role") == VIEWER
                       and now - b.get("last_seen", 0.0) <= window)

    # -------------------------------------------------------------- stats
    # The "View server stats" panel reads connection counters the handler
    # records per session id. Plain HTTP is one-way: there is no ICMP and no
    # socket close we can see, so "ping" is an approximate round-trip derived
    # from a timestamp the client echoes back on its poll, and "packet loss"
    # is the share of a session's requests that errored (a 4xx/5xx reply).

    def note_activity(self, sid, *, role=None, ip="", user_agent="",
                      bytes_in=0, bytes_out=0, error=False):
        """Record one handled request against its session id: bump the request
        count and byte totals, refresh the address/agent/role it last arrived
        with, and note the time. A request with no session id yet (a first
        unauthenticated hit before a cookie exists) is not attributed."""
        if not sid:
            return
        now = time.time()
        with self._lock:
            b = self._stats.get(sid)
            if b is None:
                b = {"sid": sid, "role": "", "ip": "", "user_agent": "",
                     "connect": now, "last_seen": now, "requests": 0,
                     "bytes_in": 0, "bytes_out": 0, "errors": 0, "rtt": 0.0}
                self._stats[sid] = b
            b["requests"] += 1
            b["bytes_in"] += int(bytes_in or 0)
            b["bytes_out"] += int(bytes_out or 0)
            if error:
                b["errors"] += 1
            if ip:
                b["ip"] = ip
            if user_agent:
                b["user_agent"] = user_agent
            if role:
                b["role"] = role
            b["last_seen"] = now

    def note_rtt(self, sid, rtt_ms):
        """Store the approximate round-trip (ms) a client's echoed timestamp
        implied on its latest poll, keeping the most recent value."""
        if not sid or rtt_ms is None or rtt_ms < 0:
            return
        with self._lock:
            b = self._stats.get(sid)
            if b is not None:
                b["rtt"] = float(rtt_ms)

    def uptime(self):
        """Seconds this host has been up (since start(), or since it was
        built if a server never came up)."""
        return time.time() - self._start_time

    def stats_snapshot(self):
        """Everything the stats dialog draws: the server header (uptime, bound
        port + URL, web flag, live status) and one row per connected session
        (name, role, IP, agent, connect/last-seen, request/byte counters,
        error rate, approximate RTT), oldest session first, plus the shared
        chat log so the panel can group messages by sender."""
        with self._lock:
            now = time.time()
            clients = []
            for sid, b in self._stats.items():
                row = dict(b)
                slot = self._seat_of(sid)
                row["name"] = ((slot or {}).get("name")
                               or self._seat_names.get(sid, "")
                               or row.get("ip", "") or sid)
                total = row["requests"] or 1
                row["error_rate"] = round(100.0 * row["errors"] / total, 1)
                clients.append(row)
            clients.sort(key=lambda r: r["connect"])
            status = self._snapshot.get("status", "")
            return {
                "uptime": now - self._start_time,
                "port": getattr(self, "port", self._want_port),
                "url": self.url,
                "web": self.web,
                "status": status,
                "game_over": status == "over",
                "has_guest": any(s["sid"] is not None for s in self._slots),
                "clients": clients,
                "messages": list(self._chat),
            }

    # -------------------------------------------------------------- auth

    def checks_password(self):
        return bool(self.password)

    def requires_password(self):
        """Whether a newcomer must still give the password to reach the game.

        A password only ever matters when one is set. After that it depends on
        the host's `lock_after_join`: when True the gate stays up for every
        visitor; when False it drops as soon as a player has taken a seat,
        so spectators can slip in and watch without the password."""
        with self._lock:
            return bool(self.password) and (self.lock_after_join
                                            or not any(s["sid"] for s in self._slots))

    def password_ok(self, candidate):
        return candidate == self.password

    @staticmethod
    def clean_nickname(name):
        """A spectator's chat name, kept short and never blank (12 chars)."""
        name = (name or "").strip()[:12]
        return name or "Spectator"


COOKIE = "flig"            # the cookie carrying a browser's role token
NICK_COOKIE = "flig_n"     # a spectator's chosen chat nickname

# role <-> the single letter actually stored in the cookie
_ROLE_VALUE = {AUTH: "a", GUEST: "g", VIEWER: "v"}
_VALUE_ROLE = {value: role for role, value in _ROLE_VALUE.items()}


def _token(handler):
    """The (role, session id) a request carries in its cookie, or (None, None)
    when it has none or the value is unknown. The cookie reads `flig=<letter>
    .<sid>`: a single letter naming the role and the id of the browser holding
    it, so a role can be checked against the connection that claimed the seat."""
    raw = handler.headers.get("Cookie") or ""
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(raw)
    except http.cookies.CookieError:
        return None, None
    morsel = jar.get(COOKIE)
    if not morsel:
        return None, None
    role_part, _, sid = morsel.value.partition(".")
    role = _VALUE_ROLE.get(role_part)
    if role is None:
        return None, None
    return role, sid


def _nickname(handler):
    """The chat nickname a spectator's browser carries, or an empty string."""
    raw = handler.headers.get("Cookie") or ""
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(raw)
    except http.cookies.CookieError:
        return ""
    morsel = jar.get(NICK_COOKIE)
    if not morsel:
        return ""
    return urllib.parse.unquote(morsel.value)


def _make_handler(host):
    """Build a request class bound to one Host, so the handler needs no
    globals: every page and endpoint reads the shared state through it."""

    class Handler(BaseHTTPRequestHandler):
        # the default banner is noisy and dates each line; a quiet log is
        # friendlier when someone is refreshing a board every second
        def log_message(self, *args):
            pass

        # ------------------------------------------------------------ plumbing
        def _begin(self):
            """Note who this request came from before routing, so the reply in
            _send can be attributed to the same session: the session id/role
            carried in its cookie, and the request's inbound byte count."""
            _role, sid = _token(self)
            self._track_sid = sid
            self._track_role = _role
            self._track_in = int(self.headers.get("Content-Length") or 0)

        def _send(self, code, body, content="application/json",
                 extra=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            ctype = (content + "; charset=utf-8"
                     if content.startswith("text") else content)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (extra or []):
                self.send_header(key, value)
            self.end_headers()
            if data:
                self.wfile.write(data)
            host.note_activity(getattr(self, "_track_sid", None),
                               role=getattr(self, "_track_role", None),
                               ip=self.client_address[0],
                               user_agent=self.headers.get("User-Agent", ""),
                               bytes_in=getattr(self, "_track_in", 0),
                               bytes_out=len(data), error=code >= 400)

        def _json(self, obj, code=200, extra=None):
            self._send(code, json.dumps(obj), extra=extra)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        # ------------------------------------------------------------- routes
        def do_GET(self):
            self._begin()
            path = urllib.parse.urlparse(self.path).path
            if path == FAVICON:
                self._asset("logo.ico", "image/x-icon")
            elif path == PIECES:
                self._asset("pieces.png", "image/png")
            elif path == PIECES2:
                self._asset("pieces2.png", "image/png")
            elif path == "/":
                # match the page on the path alone, so a shared link carrying
                # a password ("/?password=...") still serves the play page
                self._page()
            elif self.path.startswith("/api/state"):
                self._state()
            else:
                self._send(404, b"not found")

        def do_POST(self):
            self._begin()
            if self.path == "/api/login":
                self._login()
            elif self.path == "/api/join":
                self._join()
            elif self.path == "/api/register":
                self._register()
            elif self.path == "/api/move":
                self._move()
            elif self.path == "/api/resign":
                self._resign()
            elif self.path == "/api/leave":
                self._leave()
            elif self.path == "/api/chat":
                self._chat()
            else:
                self._send(404, b"not found")

        # ------------------------------------------------------------ pages
        def _page(self):
            """The one play page, always. Its steps (login -> join -> game)
            are driven by polling, so no reload is ever needed; the server
            only withholds the *board* until a password and a seat say so.
            In LAN mode there is no browser page to serve."""
            if not host.web:
                self._send(404, b"not found")
                return
            self._send(200, page.PAGE, content="text/html")

        def _asset(self, name, ctype):
            """Serve a bundled image (the favicon, the piece sprite sheet). Only
            a web host has a browser to send artwork to, so a LAN server answers
            404 - the same gate as the play page. A missing file is a 404 too."""
            if not host.web:
                self._send(404, b"not found")
                return
            try:
                data = (_ASSETS / name).read_bytes()
            except OSError:
                self._send(404, b"not found")
                return
            self._send(200, data, content=ctype)

        def _login(self):
            """Check the password and mark this browser authenticated, but do
            not claim a seat - joining is a separate, deliberate step. Mint a
            session id now so the later seat binds to this same connection."""
            body = self._read_json()
            if not host.password_ok(body.get("password", "")):
                self._json({"ok": False})
                return
            self._json({"ok": True, "role": AUTH},
                       extra=[("Set-Cookie", _cookie(AUTH, _new_sid()))])

        def _resolve(self):
            """The (role, sid) this request really acts as: a `guest` cookie
            only counts when its session id still owns the seat, so a forged or
            second guest is downgraded to a spectator. Everything that changes
            the game (move, resign, chat-as-player) is gated through here."""
            role, sid = _token(self)
            if role == GUEST and not host.has_seat(sid):
                return VIEWER, sid
            return role, sid

        def _join(self):
            """Ask for a role: the open seat to play, else spectate. A browser
            that already holds one keeps it. A player taking the seat, or one
            already seated, may carry a name (12 chars) they are shown as and
            a side (`color`) to play while the lobby still waits; a spectator
            may carry a nickname their later chat is filed under."""
            role, sid = _token(self)
            if role in (GUEST, VIEWER):
                # a stale guest whose seat was lost is really a viewer now
                if role == GUEST and not host.has_seat(sid):
                    self._json({"ok": True, "role": VIEWER})
                    return
                body = self._read_json()
                if role == GUEST:
                    name = body.get("name")
                    if name:
                        host.set_seat_name(sid, name)
                    color = body.get("color")
                    # in a cross game each seat holds one army, so asking for
                    # a side means trading seats with its holder - the server
                    # owns that, and only moves a player onto a free seat
                    if color and host.four:
                        host.reseat(sid, color)
                    # a seated player may still re-ask for a side or correct
                    # their name while the game waits for the host's Play
                    if color or name:
                        host.note_pick(sid, color=color, name=name)
                elif host.join(sid, name=body.get("name"),
                               color=body.get("color"),
                               kind=body.get("kind") or "web") == GUEST:
                    # an onlooker pressing Join again takes a seat that has
                    # come open since they started watching: nobody is left
                    # staring at a board whose slot is actually free
                    host.note_visitor()
                    self._json({"ok": True, "role": GUEST},
                               extra=[("Set-Cookie", _cookie(GUEST, sid))])
                    return
                self._json({"ok": True, "role": role})
                return
            body = self._read_json()
            sid = sid or _new_sid()
            token = host.join(sid, name=body.get("name"),
                              color=body.get("color"),
                              kind=body.get("kind") or "web")
            if token is None:
                self._json({"ok": False, "spectate": False})
                return
            host.note_visitor()
            extra = [("Set-Cookie", _cookie(token, sid))]
            if token == VIEWER:
                who = host.clean_nickname(body.get("name"))
                extra.append(("Set-Cookie", _nick_cookie(who)))
            self._json({"ok": True, "role": token}, extra=extra)

        def _state(self):
            """The board as JSON, or a lobby stub while this browser is not
            yet seated. A password is honoured until supplied; chat rides
            along, and `?since=SEQ` asks only for messages newer than that id.
            In LAN mode there are no cookies, so the lone app is seated here.
            A `guest` cookie is honoured only for the one connection holding
            the seat; when a spectator polls after that seat lapses, they are
            promoted to the player so an abandoned game can be taken over.
            """
            role, sid = _token(self)
            if host.requires_password() and role is None:
                self._json({"ok": False}, code=401)
                return
            extra = []
            token = role
            if not host.web:
                # LAN has no Join button: the connecting app is seated here,
                # and a refused seat is a hard 403 rather than a lobby screen
                token, sid = self._seat()
                if token is None:
                    self._json({"ok": False, "spectate": False}, code=403)
                    return
                if getattr(self, "_new_cookie", None):
                    extra = [("Set-Cookie", self._new_cookie)]
            elif token == GUEST and not host.has_seat(sid):
                token = VIEWER
            elif token == VIEWER and host.try_claim(sid):
                token = GUEST
                extra.append(("Set-Cookie", _cookie(GUEST, sid)))
            if token == GUEST:
                host.touch_seat(sid)          # the holder is alive: keep the seat
            if token is None or token == AUTH:
                # not seated yet: hand back a lobby so the page can offer Join,
                # with the seats on offer, the sides a player may pick and the
                # game's shape. A browser only ever sees the seats a browser
                # may take, so an app's side is not dangled in front of it.
                self._json({"lobby": True, "seat_open": host.seat_open("web"),
                            "seats": host.seats(),
                            "spectate": host.spectate,
                            "requires_password": host.requires_password(),
                            "four": host.four, "host_army": host.host_army,
                            "seat_colors": host.seat_colors("web")},
                           extra=extra)
                return
            snap = host.snapshot()
            snap["role"] = token
            snap["chat"] = host.chat(self._since())
            # the lightweight RTT echo: stamp this reply so the client can
            # send the stamp back on its next poll, from which the server
            # reads an approximate round-trip time (see _echo_ms)
            snap["t"] = int(time.time() * 1000)
            rtt = self._echo_ms()
            if sid and rtt is not None:
                host.note_rtt(sid, rtt)
            if token == GUEST:
                # the seated player is known by the name they gave for
                # themselves, not the placeholder the app hosted under; a
                # player who has none (a fresh reconnect claiming a freed
                # seat) is flagged so the page prompts before their move
                chosen = host.seat_name(sid)
                snap["guest_name"] = chosen or snap.get("guest_name", "")
                snap["need_name"] = not chosen
                # the sides this player may swap into while the lobby waits:
                # only seats opened for the kind of client holding their seat
                slot = host.seat_of(sid) or {}
                snap["seat_colors"] = host.seat_colors(slot.get("kind"))
                # in a multi-seat game the app published a view per seat's
                # army; overlay this player's own so their board, legal
                # squares and fog follow the army they actually hold
                views = snap.get("seat_views") or {}
                view = views.get(slot.get("color") or "")
                if view:
                    snap["guest_color"] = view.get("guest_color",
                                                   slot.get("color"))
                    snap["dests"] = view.get("dests", {})
                    snap["fog"] = view.get("fog", snap.get("fog", []))
                    if view.get("guest_name"):
                        snap["guest_name"] = chosen or view["guest_name"]
                    snap["you_can_move"] = view.get("you_can_move", True)
            elif token == VIEWER and host.web:
                # an onlooker is shown a seat that has come open again (its
                # player gave up or stopped polling), so a reopened slot is
                # really handed to whoever is already waiting on the game
                snap["seat_open"] = host.seat_open("web")
                snap["seat_colors"] = host.seat_colors("web")
            # keep the player's own payload lean; a spectator (and a LAN peer,
            # who is also the mover) is the one who wants the review history
            if host.web and token != VIEWER:
                snap.pop("plies", None)
            self._json(snap, extra=extra)

        def _seat(self):
            """Resolve this LAN request's role, claiming the seat for the one
            connecting app. Only used when there is no browser to press Join;
            an authenticating (AUTH) token still has to be seated. A `guest`
            cookie that no longer owns the seat is only ever a viewer."""
            role, sid = _token(self)
            self._new_cookie = None
            if role is None or role == AUTH:
                sid = sid or _new_sid()
                token = host.join(sid, kind="app")
                if token is not None:
                    host.note_visitor()
                    self._new_cookie = _cookie(token, sid)
                return token, sid
            if role == GUEST and not host.has_seat(sid):
                return VIEWER, sid
            return role, sid

        def _since(self):
            query = urllib.parse.urlparse(self.path).query
            try:
                return int(urllib.parse.parse_qs(query).get("since", ["0"])[0])
            except (ValueError, KeyError):
                return 0

        def _echo_ms(self):
            """The approximate round-trip implied by a client echoing the
            server's last `t` stamp back as `?echo=<ms>`: now minus that stamp.
            None when the poll carries no (or a nonsense) echo."""
            query = urllib.parse.urlparse(self.path).query
            try:
                echo = int(urllib.parse.parse_qs(query).get("echo", [""])[0])
            except (ValueError, KeyError):
                return None
            if echo <= 0:
                return None
            return max(0, int(time.time() * 1000) - echo)

        def _move(self):
            token, sid = self._resolve()
            body = self._read_json()
            move = {"from": body.get("from"), "to": body.get("to"),
                    "promotion": body.get("promotion")}
            ok = host.submit_move(token, move, sid=sid)
            self._json({"ok": ok})

        def _resign(self):
            token, sid = self._resolve()
            ok = host.submit_resign(token, sid=sid)
            self._json({"ok": ok})

        def _leave(self):
            """A seated player has left for good: free their seat at once so
            another can take it without waiting out the lapsed-poll timeout."""
            token, sid = self._resolve()
            ok = token == GUEST and host.drop_seat(sid)
            self._json({"ok": ok})

        def _chat(self):
            token, sid = self._resolve()
            body = self._read_json()
            name = body.get("name")
            if token == VIEWER and not name:
                name = _nickname(self)   # a spectator speaks as their nickname
            color = body.get("color")
            if token == GUEST and not color:
                slot = host.seat_of(sid) or {}
                color = slot.get("color") or host.snapshot().get(
                    "guest_color", "")
            self._json(host.submit_chat(token, body.get("text", ""),
                                        name=name, color=color, sid=sid))

        def _register(self):
            """Claim a chat name for this spectator. Only a viewer has to do
            it; on success the name is remembered and set as the nickname
            cookie so later chat is honoured without registering again."""
            token, _sid = _token(self)
            body = self._read_json()
            if token != VIEWER:
                self._json({"ok": False, "reason": "role"})
                return
            ok, value = host.register_name(body.get("name"))
            if not ok:
                self._json({"ok": False, "reason": value})
                return
            self._json({"ok": True, "name": value},
                       extra=[("Set-Cookie", _nick_cookie(value))])

    return Handler


def _cookie(role, sid=None):
    """A cookie remembering what this browser is (authenticating, playing or
    watching) and, once seated, which connection it is: `flig=<letter>.<sid>`,
    so the server can bind the playing seat to the one session that claimed it."""
    value = _ROLE_VALUE.get(role, "a")
    if sid:
        value = "%s.%s" % (value, sid)
    return "%s=%s; Path=/; SameSite=Strict" % (COOKIE, value)


def _nick_cookie(name):
    """A cookie remembering a spectator's chat nickname (percent-encoded, so
    spaces and any character survive the round trip)."""
    return "%s=%s; Path=/; SameSite=Strict" % (
        NICK_COOKIE, urllib.parse.quote(name[:12]))
