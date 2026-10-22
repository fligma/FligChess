"""The page a guest plays in: one HTML file, styled to look like Windows XP.

It draws the board from the JSON the app publishes, lets the seated guest
pick one of their pieces and then one of its legal destinations, and posts
the move back. A spectator sees the same board with the pieces but no
clicking. Everything is inline so the server has exactly one file to ship.

The board is an 8x8 grid of equal tracks inside a square container, and each
piece is a cell of the same sprite sheet the desktop app draws from
(/assets/pieces.png), scaled to the measured square width so squares stay
uniform from a phone to a desktop. Moves arrive as small event records in
the snapshot (sliding tracks, captures, mine blasts) and replay here as
animations the same ones the local window shows.
"""

# The sprite sheet is laid out as the app lays it out: two rows (black on top,
# white below) and six columns, in K Q B N R P order. A piece letter picks a
# cell by its case (upper = white = the lower row, lower = black = the upper
# row) and its face (the column); the page paints that cell as a scaled, non-
# repeating background so the browser draws the real artwork, not a text glyph.
# The cross (four-player) board adds a second sheet for the red and blue pallets
# (same 6x2 layout: red on the top row, blue on the bottom), and its squares
# carry an explicit army suffix on each code ("Pw", "Kbl") so the cell knows
# which sheet and row to read.
SPRITE_SHEET = "/assets/pieces.png"
SPRITE_SHEET2 = "/assets/pieces2.png"
SPRITE_COLS = {"k": 0, "q": 1, "b": 2, "n": 3, "r": 4, "p": 5}
# a snapshot cell that is not a playable square on the cross (a cut-out corner)
CROSS_OFF = "#"

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1,
  viewport-fit=cover">
<title>FligChess</title>
<link rel="icon" href="/favicon.ico">
<style>
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; min-height: 100%;
    font-family: Tahoma, "Segoe UI", Verdana, sans-serif; font-size: 13px;
    background: #ece9d8; color: #000; }
  body { padding: 0 0 16px; touch-action: manipulation; }
  /* the Luna gradient the XP desktop and dialogs are built from */
  .cap { background: linear-gradient(#0f5ed9, #0a4bb5 8%, #1d64e0 45%,
             #1055cc 88%, #0a3d9e);
    color: #fff; font-weight: bold; padding: 6px 10px;
    border: 1px solid #08317a; border-bottom: 0;
    text-shadow: 1px 1px 0 #1a4a9c; }
  .win { width: min(560px, 100% - 16px); margin: 12px auto;
    border: 3px solid #08317a; border-radius: 8px 8px 0 0;
    box-shadow: 0 3px 14px rgba(0,0,0,.4); background: #fff; overflow: hidden; }
  .body { padding: 12px; background: #ece9d8; }
  .status { font-weight: bold; color: #0a3d9e; margin-bottom: 8px; }
  /* the board: a sunken 8x8 with an XP-blue frame. Eight equal tracks in
     both directions inside a square wrapper make every cell a square. */
  #boardwrap { position: relative;
    width: max(240px, min(100%, 480px, calc(100vh - 250px))); margin: 0 auto; }
  .board { display: grid; grid-template-columns: repeat(8, 1fr);
    grid-template-rows: repeat(8, 1fr);
    border: 3px solid #7f9db9; background: #7f9db9; gap: 0;
    width: 100%; aspect-ratio: 1 / 1; }
  #fx { position: absolute; left: 3px; top: 3px; right: 3px; bottom: 3px;
    pointer-events: none; z-index: 5; }
  .sq { display: flex; align-items: center; justify-content: center;
    font-size: var(--gf, 34px); line-height: 1; user-select: none;
    cursor: default; overflow: hidden; }
  .sq.light { background: #eef2fb; }
  .sq.dark  { background: #94b3e6; }
  /* the cross board's four cut-out corner blocks: not squares, so nothing is
     drawn and they take the frame colour to sit back from the arms */
  .sq.off   { background: #7f9db9; }
  .sq.last  { outline: 3px solid #a6cd3c; outline-offset: -3px; }
  .sq.sel   { outline: 3px solid #f2c200; outline-offset: -3px; }
  .sq.hint  { cursor: pointer; }
  .sq.hint::after { content: ""; width: 26%; height: 26%; border-radius: 50%;
    background: rgba(20,60,140,.45); }
  /* a piece is a cell of the sprite sheet painted as a scaled, non-repeating
     background, so the browser shows the real artwork rather than a glyph.
     background-size 600% 200% tiles the 6x2 sheet; applySprite() offsets it
     to the chosen column/row. */
  .sq .p, .fly, .dead { background-repeat: no-repeat;
    background-size: 600% 200%; image-rendering: auto; }
  .sq .p { width: 88%; height: 88%; display: block; }
  /* a fogged square still holds its piece in the markup: hide the sprite so
     the veil actually veils, whatever piece sits under it */
  .sq.fog .p { visibility: hidden; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
    margin: 10px 0; }
  button { font: inherit; padding: 5px 14px; border: 1px solid #08317a;
    border-radius: 3px; background: linear-gradient(#fff, #dfe8f6);
    cursor: pointer; }
  button:hover { background: linear-gradient(#fff, #d3e2f7); }
  /* the side a player has picked reads as a pressed button */
  button.on { background: linear-gradient(#dbe7ff, #b9d0f5);
    box-shadow: inset 0 0 0 2px #0a3d9e; font-weight: bold; }
  input { font: inherit; padding: 4px 6px; border: 1px solid #7f9db9;
    background: #fff; }
  .moves { max-height: 120px; overflow: auto; -webkit-overflow-scrolling: touch;
    background: #fff; border: 1px solid #7f9db9; padding: 6px; font-size: 12px; }
  /* chat is hidden until the Chat button opens it; it then sits above the
     move history, which shrinks, and the board gives up height too, so the
     whole thing still fits the screen on a phone */
  #chatpanel { display: none; }
  .chat { max-height: 140px; overflow: auto; -webkit-overflow-scrolling: touch;
    background: #fff; border: 1px solid #7f9db9; padding: 6px; font-size: 12px;
    margin-bottom: 4px; }
  #game.chatopen #chatpanel { display: block; }
  #game.chatopen .moves { max-height: 64px; }
  #game.chatopen #boardwrap {
    width: max(220px, min(100%, 480px, calc(100vh - 430px))); }
  button:disabled { opacity: .5; cursor: default; }
  .chat .m { margin: 0 0 3px; }
  .chat .who { font-weight: bold; color: #0a3d9e; }
  .chat .me { color: #176b2b; }
  /* each chat line carries the sender's side as a small pawn drawn from
     the very sprite sheet the board uses (white, black, red and blue - the
     last two only in a four-player game). A line with no side (a
     spectator) carries no pawn. A real sprite beats a text glyph: the
     glyph's "white" pawn needed a webkit-only stroke to be visible and
     vanished on other browsers. */
  .chat .pawn { display: inline-block; width: 13px; height: 13px;
    vertical-align: -2px; background-size: 600% 200%;
    background-repeat: no-repeat; }
  /* a player who has taken over a lapsed seat must name themselves before
     their first move: this strip holds them until they do */
  #needname { background: #fff7d6; border: 1px solid #d9b400; padding: 6px;
    margin-bottom: 8px; }
  .foot { font-size: 11px; color: #555; margin-top: 8px; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 8px;
    background: #dbe7f7; border: 1px solid #94b3e6; font-size: 11px; }
  /* a seat nobody has taken yet reads as waiting, in the same amber the
     name prompt uses, so the lobby roster is legible at a glance */
  .badge.free { background: #fff7d6; border-color: #d9b400; }
  @media (max-width: 420px) {
    .body { padding: 8px; }
    .moves { max-height: 88px; }
  }
  /* ---- animations: the same moves, takes and blasts the app window shows */
  .fly { position: absolute; display: flex; align-items: center;
    justify-content: center; font-size: var(--gf, 34px); line-height: 1;
    transition: left .16s ease, top .16s ease; z-index: 6; }
  .boom { position: absolute; border-radius: 50%;
    background: radial-gradient(circle, rgba(255,255,255,.95) 0 16%,
      rgba(255,200,70,.9) 30%, rgba(255,120,20,.7) 52%,
      rgba(190,40,0,.25) 70%, rgba(190,40,0,0) 76%);
    animation: boomflash .45s ease-out forwards; }
  @keyframes boomflash {
    0% { transform: scale(.15); opacity: 0; }
    12% { opacity: 1; }
    100% { transform: scale(2.2); opacity: 0; } }
  .taken { position: absolute; border-radius: 50%;
    background: radial-gradient(circle, rgba(130,130,130,.75),
      rgba(130,130,130,0) 68%);
    animation: poof .3s ease-out forwards; }
  @keyframes poof {
    0% { transform: scale(.5); opacity: 1; }
    100% { transform: scale(1.8); opacity: 0; } }
  .dead { position: absolute; display: flex; align-items: center;
    justify-content: center; font-size: var(--gf, 34px); line-height: 1;
    animation: dieout .3s ease-out forwards; }
  @keyframes dieout {
    0% { transform: scale(1); opacity: .95; }
    100% { transform: scale(.4) rotate(28deg); opacity: 0; } }
  @keyframes shakem {
    0%, 100% { transform: translate(0, 0); }
    20% { transform: translate(-4px, 2px); }
    40% { transform: translate(4px, -2px); }
    60% { transform: translate(-3px, -2px); }
    80% { transform: translate(3px, 2px); } }
  #boardwrap.shake { animation: shakem .3s ease; }
  @media (prefers-reduced-motion: reduce) {
    .fly { transition: none; }
    .boom, .taken, .dead, #boardwrap.shake { animation: none;
      opacity: 0; } }
</style>
</head>
<body>
<div class="win">
  <div class="cap" id="cap">FligChess</div>
  <div class="body">
    <div id="login" style="display:none">
      <div class="status">This game is password protected.</div>
      <div class="row"><input id="pw" type="password" placeholder="password">
        <button onclick="sendLogin()">Enter</button></div>
      <div class="foot" id="loginerr"></div>
    </div>
    <div id="lobby" style="display:none">
      <div class="status" id="lobbytitle">FligChess</div>
      <div class="foot" id="lobbymsg"></div>
      <div class="row" id="lobbyseats" style="display:none"></div>
      <div class="row" id="nickrow" style="display:none">
        <input id="nick" maxlength="12" placeholder="nickname (optional)">
      </div>
      <div class="row" id="lobbycolors" style="display:none"></div>
      <div class="row"><button id="joinbtn" onclick="joinSeat()">Join</button></div>
    </div>
    <div id="game" style="display:none">
      <div class="status" id="status">Connecting...</div>
      <div id="needname" style="display:none">
        <div class="status">What name should we call you?</div>
        <div class="row"><input id="pname" maxlength="12" placeholder="your name">
          <button onclick="setPlayerName()">Start playing</button></div>
      </div>
      <div id="seatpick" style="display:none">
        <div class="status">Waiting for the host to press Play - your side:</div>
        <div class="row" id="pickcolors"></div>
        <div class="row"><input id="pickname" maxlength="12" placeholder="your name">
          <button onclick="applyPickName()">Set name</button>
          <button onclick="leaveSeat()">Give up seat</button></div>
      </div>
      <div id="boardwrap"><div class="board" id="board"></div><div id="fx"></div></div>
      <div class="row"><span id="you" class="badge"></span>
        <span id="role" class="badge"></span></div>
      <!-- an onlooker is offered a seat that has come open again (its player
           gave up or went quiet), so a reopened slot is never left empty -->
      <div class="row" id="takeseat" style="display:none">
        <button id="takeseatbtn" onclick="takeSeat()">Take a seat</button></div>
      <div class="row" id="reviewrow" style="display:none">
        <button onclick="prevPly()">&#9664; Prev</button>
        <span id="reviewlabel" class="badge"></span>
        <button onclick="nextPly()">Next &#9654;</button>
        <button id="jumpbtn" onclick="jumpLive()" style="display:none">Jump to live</button>
      </div>
      <div class="row" id="playrow">
        <button id="flipbtn" onclick="flip()">Flip board</button>
        <button id="chatbtn" onclick="toggleChat()">Chat</button>
        <button id="resignbtn" onclick="resign()">Resign</button></div>
      <div id="chatpanel">
        <div class="chat" id="chat"></div>
        <div class="row" id="chatname" style="display:none">
          <input id="chatnamebox" maxlength="12" placeholder="pick a chat name">
          <button onclick="registerName()">Register</button></div>
        <div class="foot" id="chatnameerr" style="display:none"></div>
        <div class="row"><input id="chatbox" placeholder="say something"
          style="flex:1 1 140px" onkeydown="if(event.key==='Enter')sendChat()">
          <button onclick="sendChat()">Send</button></div>
      </div>
      <div class="moves" id="moves"></div>
      <div class="foot" id="foot"></div>
    </div>
  </div>
</div>
<script>
var SPRITE = "__SPRITE_SHEET__";
var SPRITE2 = "__SPRITE_SHEET2__";
var PIECE_COLS = __SPRITE_COLS__;
var CROSS_OFF = "__CROSS_OFF__";
var state = null, sel = null, flipBoard = false, cells = {}, lastEv = null;
var lobby = null, viewPly = null;   // null viewPly = following the live position
var myName = null;                   // a spectator's registered chat name
var pickedColor = null;              // lobby: the side the joiner wants
var askColor = null;                 // seated: a side change not yet applied
var REDUCED = window.matchMedia &&
    matchMedia("(prefers-reduced-motion: reduce)").matches;

// Paint a piece as one cell of a sprite sheet. The sheet is 6 columns x 2 rows;
// background-size 600% 200% maps the whole sheet across the element, so a
// column sits at col/(6-1) and a row at row/(2-1) of the position range. On the
// two-player board a plain letter names the cell by its case (upper = white =
// the lower row of pieces.png, lower = black = its upper row). A cross square
// carries an army suffix ("Pw", "Kbl"): white/black read pieces.png (rows 1/0),
// red/blue read pieces2.png (rows 0/1). The face column is shared by both.
function applySprite(el, piece) {
  if (!el || !piece || piece === ".") return;
  var col = PIECE_COLS[piece[0].toLowerCase()];
  if (col === undefined) return;
  var army = piece.slice(1), sheet, row;
  if (army === "w") { sheet = SPRITE; row = 1; }
  else if (army === "b") { sheet = SPRITE; row = 0; }
  else if (army === "r") { sheet = SPRITE2; row = 0; }
  else if (army === "bl") { sheet = SPRITE2; row = 1; }
  else { sheet = SPRITE; row = piece === piece.toUpperCase() ? 1 : 0; }
  el.style.backgroundImage = "url(" + sheet + ")";
  el.style.backgroundPosition = (col * 20) + "% " + (row * 100) + "%";
}

function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)}).then(function (r) { return r.json(); });
}
function show(which) {
  ["login", "lobby", "game"].forEach(function (p) {
    document.getElementById(p).style.display = p === which ? "" : "none";
  });
}
function sendLogin() {
  // the cookie is set by the server; no reload - poll() picks the new role up
  post("/api/login", {password: document.getElementById("pw").value})
    .then(function (j) {
      if (j && j.ok) { document.getElementById("loginerr").textContent = ""; poll(); }
      else document.getElementById("loginerr").textContent = "Wrong password.";
    });
}

// -------------------------------------------------------------- lobby / join
// One page, three steps: login -> lobby -> game, all driven by poll(). A
// browser is never seated by loading the page; it presses Join. The first
// Joiner takes the open seat, everyone after is a spectator (if allowed).
// While the seat is open the joiner may also pick the side (colour or army)
// they want to play and type their own name; the host applies both in the
// waiting room, and the seated player can change them again until Play.
function colorLabel(c) {
  return c === "w" ? "White" : c === "b" ? "Black" :
      c === "r" ? "Red" : c === "bl" ? "Blue" : c;
}
function kindLabel(k) {
  return k === "app" ? "app player" : k === "web" ? "web player" : "player";
}
// who holds what, read straight off the server: the host's own corner plus
// one badge per opened seat, so a joiner sees which sides are still free (and
// that a seat kept for other FligChess apps is not theirs to take) before
// pressing Join. A plain two-player game has nothing to list, so it stays hidden.
function renderSeatList(el, seats, hostArmy) {
  el.innerHTML = "";
  var rows = [];
  if (hostArmy) rows.push({color: hostArmy, kind: "host", taken: true, name: ""});
  (seats || []).forEach(function (s) { if (s.color) rows.push(s); });
  if (rows.length < 2) { el.style.display = "none"; return; }
  rows.forEach(function (s) {
    var b = document.createElement("span");
    var text = colorLabel(s.color) + ": ";
    if (s.kind === "host") text += "host";
    else if (s.taken) text += (s.name || "seated");
    else text += "open - " + kindLabel(s.kind);
    b.className = "badge" + (s.taken || s.kind === "host" ? "" : " free");
    b.textContent = text;
    el.appendChild(b);
  });
  el.style.display = "";
}
function colorButtons(el, colors, current, choose) {
  el.innerHTML = "";
  colors.forEach(function (c) {
    var b = document.createElement("button");
    b.textContent = colorLabel(c);
    if (c === current) b.className = "on";
    b.onclick = function () { choose(c); };
    el.appendChild(b);
  });
}
function renderLobby() {
  var open = lobby.seat_open;
  var many = (lobby.seats || []).length > 1;    // a cross game with several seats
  var msg = document.getElementById("lobbymsg");
  var nick = document.getElementById("nickrow");
  var btn = document.getElementById("joinbtn");
  var colors = document.getElementById("lobbycolors");
  btn.disabled = false;
  renderSeatList(document.getElementById("lobbyseats"), lobby.seats,
                 lobby.four ? lobby.host_army : null);
  if (open) {
    document.getElementById("lobbytitle").textContent =
        many ? "Seats are open" : "A seat is open";
    msg.textContent = "Join to play this game yourself. Pick a side if you " +
        "care which one; once every seat is filled anyone after watches as a " +
        "spectator.";
    btn.textContent = many ? "Join as a player" : "Join as the player";
    // the player names themselves here; the host never picks it for them
    document.getElementById("nick").placeholder = "your name (optional)";
    nick.style.display = "";
    colorButtons(colors, lobby.seat_colors || [], pickedColor,
                 function (c) { pickedColor = c; renderLobby(); });
    colors.style.display = colors.children.length ? "" : "none";
  } else if (lobby.spectate) {
    document.getElementById("lobbytitle").textContent = "FligChess";
    msg.textContent = "You can watch this game and chat as a spectator.";
    btn.textContent = "Join as spectator";
    document.getElementById("nick").placeholder = "nickname (optional)";
    nick.style.display = "";
    colors.innerHTML = "";
    colors.style.display = "none";
  } else {
    document.getElementById("lobbytitle").textContent = "FligChess";
    msg.textContent = "Every seat is taken and the host has closed spectating.";
    btn.textContent = "Join";
    btn.disabled = true;
    nick.style.display = "none";
    colors.style.display = "none";
  }
}
function joinSeat() {
  var body = {};
  // both a player taking the seat and a spectator pass along the name they
  // typed; a player who leaves it blank is prompted once inside the game,
  // and a player who picked a side asks for it (the host honours it before
  // the game starts). This is a browser, and says so: the server only seats
  // it on a side the host opened for web players.
  body.kind = "web";
  if (lobby.seat_open || lobby.spectate)
    body.name = (document.getElementById("nick").value || "").slice(0, 12);
  if (lobby.seat_open && pickedColor) body.color = pickedColor;
  post("/api/join", body).then(function (j) {
    if (j && j.ok) poll();
    else document.getElementById("lobbymsg").textContent =
        "No room: every seat is taken and spectating is off.";
  });
}
// giving up the seat deliberately: the slot comes open for the next player
// at once instead of waiting for this browser's polls to lapse, and this
// browser goes on watching the game it left (no reload - the poll re-roles it)
function leaveSeat() {
  if (!window.confirm("Give up your seat and watch instead?")) return;
  post("/api/leave", {}).then(function () { poll(); });
}
function setPlayerName() {
  var box = document.getElementById("pname");
  var name = (box.value || "").trim().slice(0, 12);
  if (!name) { box.focus(); return; }
  // an already-seated player re-posting Join just records their own name
  post("/api/join", {name: name}).then(function () { poll(); });
}
// the seated player's side/name strip: shown while the game waits for the
// host's Play, so a LAN player can settle into the side they like first
function renderSeatPick() {
  var sp = document.getElementById("seatpick");
  if (!sp) return;
  var show = state.role === "guest" && state.status === "waiting"
      && !state.need_name;
  sp.style.display = show ? "" : "none";
  if (!show) return;
  // our own ask stays pressed until the host applies it and the snapshot
  // agrees; after that the server's answer is the truth
  if (askColor && askColor === state.guest_color) askColor = null;
  var cur = askColor || state.guest_color;
  colorButtons(document.getElementById("pickcolors"),
               state.seat_colors || [], cur, function (c) {
    if (c === cur) return;
    askColor = c;
    post("/api/join", {color: c}).then(function () { poll(); });
    render();
  });
  var nm = document.getElementById("pickname");
  if (document.activeElement !== nm) nm.value = state.guest_name || "";
}
function applyPickName() {
  var nm = document.getElementById("pickname");
  var name = (nm.value || "").trim().slice(0, 12);
  if (!name) { nm.focus(); return; }
  post("/api/join", {name: name}).then(function () { poll(); });
}
// an onlooker's offer: while a seat the host opened for browsers stands free
// (never taken, or given up part-way through) the spectator may claim it. The
// button names the side they would take, so nothing is a surprise.
function renderTakeSeat() {
  var el = document.getElementById("takeseat");
  if (!el) return;
  var colors = state.seat_colors || [];
  var show = state.role === "viewer" && state.seat_open && colors.length;
  el.style.display = show ? "" : "none";
  if (show) {
    el.dataset.color = colors[0];
    document.getElementById("takeseatbtn").textContent =
        "Take the " + colorLabel(colors[0]) + " seat";
  }
}
function takeSeat() {
  var el = document.getElementById("takeseat");
  var body = {kind: "web"};
  if (el.dataset.color) body.color = el.dataset.color;
  if (myName) body.name = myName;
  post("/api/join", body).then(function () { poll(); });
}

// -------------------------------------------------------------- move review
// The snapshot carries every past position (plies[0] is the start, plies[k]
// the board after k moves). viewPly picks one; null means "follow live".
function reviewCount() { return (state && state.plies) ? state.plies.length : 0; }
function livePly() { return reviewCount() - 1; }
function atLive() { return viewPly === null || viewPly >= livePly(); }
function prevPly() {
  var n = livePly();
  if (n < 1) return;
  viewPly = (viewPly === null ? n : viewPly) - 1;
  if (viewPly < 0) viewPly = 0;
  render();
}
function nextPly() {
  if (viewPly === null) return;
  viewPly++;
  if (viewPly >= livePly()) viewPly = null;
  render();
}
function jumpLive() { viewPly = null; render(); }
function sqName(c, r) { return String.fromCharCode(97 + c) + (r + 1); }

// The union view a spectator is shown tints each visible tile by the armies
// that can see it, reusing the same hues as the app's ARMY_TINT so an onlooker
// reads "who sees what" the same way as on the desktop. A lone owner paints
// that team's hue; several blend toward an average, exactly as _blend_tint.
var ARMY_TINT = {w: [240, 240, 240], b: [40, 40, 40],
                 r: [200, 60, 50], bl: [60, 110, 200]};
// the plain checker colours (match .sq.light / .sq.dark above), blended with
var TINT_BASE = {light: [238, 242, 251], dark: [148, 179, 230]};
function armyRgb(owners) {
  var cols = (owners || []).map(function (o) {
    return ARMY_TINT[o] || [128, 128, 128];
  });
  if (!cols.length) cols = [[128, 128, 128]];
  var n = cols.length, s = [0, 0, 0];
  for (var j = 0; j < n; j++)
    for (var i = 0; i < 3; i++) s[i] += cols[j][i];
  return [Math.round(s[0] / n), Math.round(s[1] / n), Math.round(s[2] / n)];
}
function tintedBackground(rgb, lightOn) {
  var base = TINT_BASE[lightOn ? "light" : "dark"], a = 0.42, out = [];
  for (var i = 0; i < 3; i++)
    out.push(Math.round(base[i] * (1 - a) + rgb[i] * a));
  return "rgb(" + out.join(",") + ")";
}

function render() {
  if (!state) return;
  var board = document.getElementById("board");
  board.innerHTML = "";
  cells = {};
  var viewer = state.role === "viewer";
  var rc = reviewCount();
  if (viewPly !== null && viewPly > livePly()) viewPly = null;   // clamp to live
  var review = rc > 0 && !atLive();
  var snap = review ? state.plies[viewPly] : null;
  var four = !!state.four;
  var N = four ? (state.size || 14) : 8;
  var last = (snap ? snap.last : state.last) || [];
  var youCan = state.role === "guest" && state.you_can_move && !review
      && !state.need_name;
  // no clicking while reviewing, and only the seated player ever gets hints:
  // a spectator is read-only, the server already refuses their moves
  var dests = (state.role !== "guest" || review) ? {} : (state.dests || {});
  // the fog to veil and the tint to paint depend on who is looking and whether
  // they are scrubbing back: a seated player sees only its own fog (the server
  // already blanked every hidden piece), a spectator the union fog plus the
  // per-tile team tint, and a review ply keeps the fog captured under it
  var fog, tint = {};
  if (review) {
    fog = (snap && snap.fog) || [];
    tint = (snap && snap.tint) || {};
  } else if (viewer) {
    fog = state.spec_fog || [];
    tint = state.tint || {};
  } else {
    fog = state.fog || [];
  }
  // the grid of piece codes: for the cross, cells[y][x] with y 0 the north
  // edge; for the two-player board, rows[r] are eight strings with r 0 the
  // top rank (rank 8). Empty cells are '.'; the cross marks its four cut-out
  // corner blocks CROSS_OFF and draws nothing there.
  var grid = snap ? (four ? snap.cells : snap.rows)
                  : (four ? state.cells : state.rows);
  board.className = "board" + (four ? " cross" : "");
  board.style.gridTemplateColumns = "repeat(" + N + ", 1fr)";
  board.style.gridTemplateRows = "repeat(" + N + ", 1fr)";
  // the two-player board can be flipped so the guest's pieces sit at the
  // bottom; the cross keeps a fixed orientation (black north, white south)
  var flip = !four && flipBoard !== (state.guest_color === "b");
  for (var r = 0; r < N; r++) {
    for (var c = 0; c < N; c++) {
      var cell = document.createElement("div");
      if (four && grid[r][c] === CROSS_OFF) {
        cell.className = "sq off";
        board.appendChild(cell);
        continue;
      }
      var name, piece, lightOn;
      if (four) {
        name = sqName(c, r);                 // c = x file, r = y row (0-based)
        piece = grid[r][c];
        lightOn = ((c + r) % 2) === 0;        // parity matches the app's cross
      } else {
        var br = flip ? 7 - r : r, bc = flip ? 7 - c : c;
        name = sqName(bc, 7 - br);
        piece = grid[br][bc];
        lightOn = ((bc + (7 - br)) % 2) === 1;
      }
      cell.className = "sq " + (lightOn ? "light" : "dark");
      if (fog.indexOf(name) >= 0) {
        cell.classList.add("fog");      // a defense-in-depth veil: the piece
      } else if (tint[name]) {          // is already blanked on the server
        cell.style.background =
            tintedBackground(armyRgb(tint[name]), lightOn);
      }
      if (piece && piece !== ".") {
        var s = document.createElement("span");
        s.className = "p";
        applySprite(s, piece);
        cell.appendChild(s);
      }
      if (last.indexOf(name) >= 0) cell.classList.add("last");
      if (sel === name) cell.classList.add("sel");
      if (sel && (dests[sel] || []).indexOf(name) >= 0) {
        cell.classList.add("hint");
        (function (from, to) {
          cell.onclick = function () { play(from, to); };
        })(sel, name);
      } else if (youCan && dests[name]) {
        (function (nm) {
          cell.onclick = function () { sel = nm; render(); };
        })(name);
      }
      board.appendChild(cell);
      cells[name] = cell;
    }
  }
  // one glyph size for every square, measured rather than guessed, so
  // pieces always fit their cell on any screen
  var gf = (board.clientWidth || 288) / N * 0.8;
  document.documentElement.style.setProperty("--gf", gf + "px");
  document.getElementById("status").textContent = statusLine();
  var over = !!state.result || state.status === "over";
  // the "you" badge: what this browser can do right now
  var you = document.getElementById("you");
  if (review) you.textContent = "Reviewing";
  else if (state.role === "guest")
      you.textContent = youCan ? "Your move" : (over ? "Game over" : "Waiting...");
  else you.textContent = over ? "Game over" :
      (armyName(state.turn) + " to move");
  // the role badge says plainly whether this browser is playing or watching
  document.getElementById("role").textContent =
      viewer ? "Spectating" : "You are playing";
  // move review strip: shown whenever there is more than one position
  var rr = document.getElementById("reviewrow");
  rr.style.display = rc > 1 ? "" : "none";
  document.getElementById("reviewlabel").textContent = review
      ? ("Move " + viewPly + " / " + livePly())
      : ("Move " + Math.max(0, livePly()) + " / " + Math.max(0, livePly()) + " (latest)");
  document.getElementById("jumpbtn").style.display = review ? "" : "none";
  // a spectator sees only the board, chat and the scrubber: no move list, no
  // flip, no resign; only the seated player can resign, and only while live
  document.getElementById("moves").style.display = viewer ? "none" : "";
  document.getElementById("moves").innerHTML = moveList();
  document.getElementById("flipbtn").style.display =
      (viewer || state.four) ? "none" : "";
  var rb = document.getElementById("resignbtn");
  rb.style.display = (state.role === "guest" && !over) ? "" : "none";
  document.getElementById("foot").textContent = state.status === "over"
      ? "This game has finished." : "";
  document.getElementById("cap").textContent = "FligChess - " +
      (state.variant || "") + " - " + (state.host_name || "Host") +
      " vs " + (state.guest_name || "Guest");
  // the browser tab names the role: a player sees "Player", an onlooker
  // "Spectator", so it is obvious at a glance who is who
  document.title = "FligChess - " + (viewer ? "Spectator" : "Player");
  // a player who has taken over a lapsed seat is held here until they name
  // themselves; no move is possible while the strip is up
  var need = document.getElementById("needname");
  if (need) {
    var gated = state.role === "guest" && state.need_name;
    need.style.display = gated ? "" : "none";
    if (gated) document.getElementById("pname").focus();
  }
  renderSeatPick();
  renderTakeSeat();
  renderChat();
  if (review) {
    // keep the event cursor current so returning to live never replays old fx
    var evs = state.events || [];
    if (evs.length) lastEv = evs[evs.length - 1].id;
  } else {
    playEvents();
  }
}
function armyName(a) {
  // the cross snapshot carries a friendly name per army; fall back to the
  // two-player wording so a plain w/b turn still reads out correctly
  if (state && state.army_names && state.army_names[a]) return state.army_names[a];
  return a === "b" ? "Black" : a === "r" ? "Red" : a === "bl" ? "Blue" :
      a === "w" ? "White" : a;
}
function statusLine() {
  if (state.result) return "Result: " + state.result;
  if (state.status === "waiting") return state.four
      ? "Waiting for players to join..." : "Waiting for a guest to join...";
  return armyName(state.turn) + " to move" +
      (state.check ? " - check!" : "");
}
function moveList() {
  var m = state.moves || [], out = [];
  for (var i = 0; i < m.length; i += 2)
    out.push((i / 2 + 1) + ". " + m[i] + " " + (m[i + 1] || ""));
  return out.join("<br>") || "&nbsp;";
}
function play(from, to) {
  sel = null;
  post("/api/move", {from: from, to: to}).then(function () { poll(); });
}
function flip() { flipBoard = !flipBoard; render(); }

// The Chat button shows or hides the chat panel, which sits above the move
// history; opening it lets the history shrink so everything stays on screen.
function toggleChat() {
  var g = document.getElementById("game");
  var open = g.classList.toggle("chatopen");
  document.getElementById("chatbtn").textContent = open ? "Hide chat" : "Chat";
  if (open) { renderChat(); document.getElementById("chatbox").focus(); }
}
// Resign ends the game with the guest losing; ask first, it cannot be undone.
function resign() {
  if (!window.confirm("Resign this game?")) return;
  post("/api/resign", {}).then(function () { poll(); });
}

// ------------------------------------------------------------------- chat
// The snapshot carries the shared message log; a full re-render is cheap and
// keeps a late joiner seeing the whole conversation. Sending posts the text
// and refreshes at once, so it reads back on the very next poll.
function renderChat() {
  var box = document.getElementById("chat");
  var msgs = state.chat || [];
  box.innerHTML = msgs.map(function (m) {
    // each line is marked with a small pawn sprite in the sender's army
    // colour so host, player and (in four-player) red/blue lines read
    // apart at a glance; the same pallet the board draws its pieces from
    var code = {w: "P", b: "p", r: "Pr", bl: "Pbl"}[m.color];
    var mark = code ? "<span class='pawn' data-p='" + code + "'></span> "
        : "";
    return "<div class='m'>" + mark + "<span class='who'>" + esc(m.who) +
        "</span> " + esc(m.text) + "</div>";
  }).join("") || "<span class='foot'>No messages yet.</span>";
  box.querySelectorAll(".pawn").forEach(function (el) {
    applySprite(el, el.getAttribute("data-p"));
  });
  box.scrollTop = box.scrollHeight;
  // a spectator with no registered name is prompted for one; the seated
  // player speaks as their host-assigned name and is never asked
  var row = document.getElementById("chatname");
  if (row) row.style.display =
      (state.role === "viewer" && !myName) ? "" : "none";
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; });
}
function nameErr(msg) {
  var e = document.getElementById("chatnameerr");
  if (!e) return;
  e.textContent = msg || "";
  e.style.display = msg ? "" : "none";
}
function showNameRow(msg) {
  var row = document.getElementById("chatname");
  if (row) row.style.display = "";
  nameErr(msg || "");
  var box = document.getElementById("chatnamebox");
  if (box && !box.value) box.focus();
}
function sendChat() {
  var box = document.getElementById("chatbox");
  var text = (box.value || "").trim();
  if (!text) return;
  if (state && state.role === "viewer" && !myName) {
    showNameRow("Pick a chat name before you talk.");   // keep the draft
    return;
  }
  box.value = "";
  post("/api/chat", {text: text}).then(function (j) {
    if (j && j.need_name) {
      document.getElementById("chatbox").value = text;   // nothing lost
      showNameRow("Pick a chat name before you talk.");
    }
    poll();
  });
}
function registerName() {
  var box = document.getElementById("chatnamebox");
  var name = (box.value || "").trim();
  if (!name) { nameErr("Enter a name first."); return; }
  post("/api/register", {name: name}).then(function (j) {
    if (j && j.ok) {
      myName = j.name;
      document.getElementById("chatname").style.display = "none";
      nameErr("");
      poll();
    } else if (j && j.reason === "taken") {
      nameErr("That name is taken.");     // keep the typed text to edit
    } else if (j && j.reason === "invalid") {
      nameErr("Enter a name first.");
    } else {
      nameErr("Could not register that name.");
    }
  });
}

// ---------------------------------------------------------------- animations
// The app journals each move as {id, tracks, capture, blast}. Anything with
// a newer id than the last one seen gets played out over the board once,
// from the current cell positions, so it works at any window size.
function rectOf(name) {
  var el = cells[name];
  if (!el) return null;
  var f = document.getElementById("fx").getBoundingClientRect();
  var r = el.getBoundingClientRect();
  return {x: r.left - f.left, y: r.top - f.top, w: r.width, h: r.height};
}
function fogged(name) {
  return (state.fog || []).indexOf(name) >= 0;
}
function playEvents() {
  var evs = state.events || [];
  var top = evs.length ? evs[evs.length - 1].id : 0;
  if (lastEv === null) { lastEv = top; return; }   // joining: show nothing old
  if (top > lastEv) {
    var fresh = evs.filter(function (e) { return e.id > lastEv; });
    lastEv = top;
    if (!REDUCED) fresh.slice(-2).forEach(function (ev, i) {
      setTimeout(function () { animateMove(ev); }, i * 200);
    });
  }
}
function animateMove(ev) {
  var tracks = ev.tracks || [];
  if (!tracks.length) return;
  var hidden = tracks.some(function (t) { return fogged(t[1]) || fogged(t[2]); });
  if (hidden) { effects(ev, 0); return; }          // fog: no sliding, just fx
  var landed = 0;
  tracks.forEach(function (t, i) {
    var piece = t[0], ra = rectOf(t[1]), rb = rectOf(t[2]);
    if (!ra || !rb) return;
    var settled = cells[t[2]].firstChild;
    if (settled) settled.style.visibility = "hidden";   // the clone carries it
    var s = document.createElement("span");
    s.className = "fly";
    applySprite(s, piece);
    s.style.left = ra.x + "px"; s.style.top = ra.y + "px";
    s.style.width = ra.w + "px"; s.style.height = ra.h + "px";
    document.getElementById("fx").appendChild(s);
    void s.offsetWidth;                            // one frame, then slide
    s.style.left = rb.x + "px"; s.style.top = rb.y + "px";
    s.addEventListener("transitionend", function () {
      s.remove();
      if (settled) settled.style.visibility = "";
      landed++;
      if (landed === tracks.length) effects(ev, 0);
    });
  });
  // a tab that skips frames may fire no transitionend: land by the clock too
  setTimeout(function () {
    tracks.forEach(function (t) {
      var c = cells[t[2]], k = c && c.firstChild;
      if (k && k.style.visibility === "hidden") k.style.visibility = "";
    });
    var stuck = document.getElementById("fx").querySelector(".fly");
    if (stuck) effects(ev, 0);
    document.querySelectorAll(".fly").forEach(function (s) { s.remove(); });
  }, 400);
}
function effects(ev, delay) {
  setTimeout(function () {
    if (ev._fx) return;                         // land exactly once per event
    ev._fx = true;
    var fx = document.getElementById("fx");
    var blasts = ev.blast || [];
    if (blasts.length) {                        // a mine went off: fire and shake
      blasts.forEach(function (name) { puff(name, "boom", 1.7); });
      var wrap = document.getElementById("boardwrap");
      wrap.classList.remove("shake");
      void wrap.offsetWidth;
      wrap.classList.add("shake");
      return;
    }
    if (ev.capture) {                           // a take: smoke and a fading victim
      puff(ev.capture[0], "taken", 1.3);
      if (ev.capture[1] && !fogged(ev.capture[0]))
        puffPiece(ev.capture[0], ev.capture[1]);
    }
  }, delay);
}
function puff(name, cls, scale) {
  var r = rectOf(name);
  if (!r) return;
  var d = document.createElement("div");
  d.className = cls;
  d.style.left = (r.x - r.w * (scale - 1) / 2) + "px";
  d.style.top = (r.y - r.h * (scale - 1) / 2) + "px";
  d.style.width = (r.w * scale) + "px";
  d.style.height = (r.h * scale) + "px";
  document.getElementById("fx").appendChild(d);
  d.addEventListener("animationend", function () { d.remove(); });
  setTimeout(function () { d.remove(); }, 800);    // in case it never fires
}
// the fading victim of a take carries its sprite, like a board piece
function puffPiece(name, piece) {
  var r = rectOf(name);
  if (!r) return;
  var d = document.createElement("div");
  d.className = "dead";
  applySprite(d, piece);
  d.style.left = r.x + "px"; d.style.top = r.y + "px";
  d.style.width = r.w + "px"; d.style.height = r.h + "px";
  document.getElementById("fx").appendChild(d);
  d.addEventListener("animationend", function () { d.remove(); });
  setTimeout(function () { d.remove(); }, 800);
}

var polling = false;
var lastEcho = null;          // the server's last stamp, echoed to time a poll
function poll() {
  if (polling) return;                 // one loop only, however often it's called
  polling = true;
  // echo the stamp the server sent on the previous poll so it can report an
  // approximate round-trip; on the first poll there is nothing to send back
  var url = "/api/state" + (lastEcho ? ("?echo=" + lastEcho) : "");
  fetch(url).then(function (r) {
    if (r.status === 401) { show("login"); return null; }   // gate: needs password
    return r.json();
  }).then(function (j) {
    if (!j) return;
    if (j.t) lastEcho = j.t;           // remember the stamp for the next round
    if (j.lobby) { lobby = j; show("lobby"); renderLobby(); return; }
    state = j;
    // a spectator or player following live stays live; a stepped-back viewer
    // keeps their place as the game runs on without them
    if (viewPly !== null && viewPly >= livePly()) viewPly = null;
    show("game");
    render();
  }).catch(function () {
    // a hiccup (host restarting, a dropped request) must never freeze the
    // page back to a bare titlebar: keep the board up and try again
    if (!state) document.getElementById("status").textContent =
        "Connecting...";
  }).then(function () { polling = false; setTimeout(poll, 600); });
}
// boot: show the board area at once so the page is never just a titlebar,
// then fill it; poll() routes to login/lobby/game as the server answers
show("game");
// a player who closes the tab hands their seat back at once rather than making
// the next person wait for their polls to lapse
window.addEventListener("pagehide", function () {
  if (!state || state.role !== "guest") return;
  try {
    fetch("/api/leave", {method: "POST", keepalive: true,
      headers: {"Content-Type": "application/json"}, body: "{}"});
  } catch (e) { /* the seat still opens itself when the polls stop */ }
});
// A shared link can carry the password (?password=...): log in up front so
// the guest lands on the lobby, not the gate. Taking a seat is still a
// deliberate press of Join, exactly like typing the password by hand.
(function () {
  var pw = new URLSearchParams(location.search).get("password");
  if (pw) { post("/api/login", {password: pw}).then(function () { poll(); }); }
  else poll();
})();
</script>
</body>
</html>
""".replace("__SPRITE_SHEET__", SPRITE_SHEET).replace("__SPRITE_SHEET2__",
                                                       SPRITE_SHEET2).replace(
    "__SPRITE_COLS__", repr(SPRITE_COLS)).replace("__CROSS_OFF__", CROSS_OFF)


# the tiny page shown instead of the board when spectating is switched off
SPECTATE_OFF = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FligChess</title><style>body{font-family:Tahoma,sans-serif;
background:#ece9d8;padding:28px}h2{color:#0a3d9e}</style></head>
<body><h2>FligChess</h2><p>The host has turned spectating off, so this game
is not open to onlookers. Ask them to switch it on if you would like to
watch.</p></body></html>"""


# the login screen, served when a password is set and not yet supplied
LOGIN = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FligChess</title><style>
body{font-family:Tahoma,sans-serif;background:#ece9d8;display:flex;
justify-content:center;padding-top:40px}
.win{width:min(320px,100% - 16px);border:3px solid #08317a;
border-radius:8px 8px 0 0;background:#fff;box-shadow:0 3px 14px rgba(0,0,0,.4)}
.cap{background:linear-gradient(#1d64e0,#0a3d9e);color:#fff;font-weight:bold;
padding:6px 10px;text-shadow:1px 1px 0 #1a4a9c}
.body{padding:16px;background:#ece9d8}
input{width:100%;padding:5px;border:1px solid #7f9db9;box-sizing:border-box}
button{margin-top:12px;padding:5px 16px;border:1px solid #08317a;
border-radius:3px;background:linear-gradient(#fff,#dfe8f6);cursor:pointer}
.err{color:#b00000;margin-top:8px;font-size:12px}</style></head>
<body><div class="win"><div class="cap">FligChess</div><div class="body">
<p>This game is password protected.</p>
<input id="pw" type="password" autofocus placeholder="password">
<div><button onclick="go()">Enter</button></div>
<div class="err" id="e"></div></div></div>
<script>
function go(){fetch('/api/login',{method:'POST',
 headers:{'Content-Type':'application/json'},
 body:JSON.stringify({password:document.getElementById('pw').value})})
.then(function(r){return r.json();}).then(function(j){
 if(j.ok)location.reload();else document.getElementById('e').textContent=
 'Wrong password.';});}
document.getElementById('pw').addEventListener('keydown',function(e){
 if(e.key==='Enter')go();});
</script></body></html>"""
