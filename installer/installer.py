"""The FligChess installer - a small pygame app in the same XP Luna theme.

It asks GitHub for the newest release, shows its version and date, lets you
pick where to install (a built-in folder browser, no native dialog so the
whole thing stays stdlib + pygame), downloads and unpacks that release's
zip there, and drops a desktop shortcut carrying the app logo. An optional
token field carries a GitHub token in case the anonymous API limit is hit
(or the repo is private); it lands in the install's config/updater.json so
the in-app updaters reuse it. Everything heavy (the release lookup and the
download) runs on a worker thread so the window never freezes.

Run from a checkout with the app on the path:
    PYTHONPATH=src python installer/installer.py
The build workflow packages this file with PyInstaller into the
FligChess-Installer executable that ships beside the app itself.
"""

import os
import ctypes
import ctypes.util
import json
import pathlib
import subprocess
import sys
import threading
import time
import traceback

# make the fligchess package importable when run straight from a checkout
_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pygame                                  # noqa: E402

try:                                            # borderless window tricks
    from pygame._sdl2 import video as _sdl2     # noqa: E402
except ImportError:
    _sdl2 = None                                 # noqa: E402

from fligchess import const               # noqa: E402  (ASSETS_DIR is package-relative)
from fligchess.core import appinstall     # noqa: E402
from fligchess.core import releases       # noqa: E402
from fligchess.ui import xp               # noqa: E402
from fligchess.version import APP_VERSION  # noqa: E402

WIN = (560, 420)                  # default size; the window is resizable
MIN_WIN = (460, 360)              # never lay out below this
TITLEBAR_H = 28
EDGE_GRAB = 6                     # band along the border that resizes
UI_FONT = "tahoma,verdana,segoeui,dejavusans"

# the bubble shown when hovering the token field's "?": when a token is
# actually needed, what kind works, and where the installer puts it
TOKEN_TIP = (
    "Optional GitHub token.",
    "GitHub allows only 60 anonymous API calls per hour per",
    "IP, and a private repo needs a token anyway. If the",
    "release lookup above fails (rate limit or needs a",
    "token), paste one here and press Enter to look again.",
    "A classic token with just the \"repo\" scope, or a",
    "fine-grained one with read access to repository",
    "contents, is enough.",
    "It is also saved to config/updater.json inside the",
    "install folder, so the app's bot updater uses it too.",
)


def _set_win_app_id():
    """Claim our own Windows taskbar button (the main app does the same):
    without an explicit AppUserModelID the shell groups the window under
    whatever launched it and shows the generic pygame feather instead of
    the FligChess logo. Windows only, and never fatal."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Fligma.FligChess.Installer")
    except (OSError, AttributeError):
        pass


def _load_sdl_dll():
    """The SDL2 library pygame links against, only for the desktop-wide mouse
    position (the one stable anchor while a borderless window slides under the
    cursor). None when it cannot be found, so dragging simply degrades. A
    PyInstaller bundle hoists the library out of pygame's own folder, so the
    bundle root, the loader's default search and the system lookup all get a
    try - the same ladder the app uses, and the reason the two stay in step."""
    if sys.platform == "win32":
        names = ["SDL2.dll"]
    elif sys.platform == "darwin":
        names = ["libSDL2.dylib", "libSDL2-2.0.0.dylib"]
    else:
        names = ["libSDL2-2.0.so.0", "libSDL2-2.0.so", "libSDL2.so"]
    roots = [pathlib.Path(pygame.__file__).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(pathlib.Path(meipass))
    for root in roots:
        for name in names:
            path = root / name
            if path.exists():
                try:
                    return ctypes.CDLL(str(path))
                except OSError:
                    pass
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError:
            pass
    try:
        found = ctypes.util.find_library("SDL2") or ctypes.util.find_library(
            names[0])
        if found:
            return ctypes.CDLL(found)
    except (OSError, ValueError):
        pass
    return None


class Installer:
    def __init__(self):
        _set_win_app_id()           # before the window: taskbar grouping
        pygame.init()
        self.size = WIN
        self.screen = pygame.display.set_mode(self.size, pygame.RESIZABLE)
        pygame.display.set_caption("Install FligChess")
        self.font = pygame.font.SysFont(UI_FONT, 14)
        self.big = pygame.font.SysFont(UI_FONT, 18, bold=True)
        self.small = pygame.font.SysFont(UI_FONT, 12)
        self.clock = pygame.time.Clock()
        self.running = True
        self._close_btn = None           # caption close box, set on first draw
        self._win = None                 # the one live SDL window wrapper
        self._dll = _load_sdl_dll()      # for the global mouse while dragging
        # a Wayland compositor ignores a client's request to move its window,
        # so there the installer keeps the native titlebar - the drawn one
        # would be a handle attached to a window that cannot be dragged
        self._movable = const.client_can_place_window(self._video_driver())
        self._drag = None                # active move/resize grab, see _drag_start
        self._win_pending = None         # {"pos", "size"} to apply outside the pump
        self._layout()
        self.logo, self.icon = self._load_logo()
        self._apply_icon_and_chrome()
        # release lookup, filled in by a worker thread
        self.release = None
        self.release_err = None
        # optional GitHub token: only needed past the anonymous API limit
        # or for a private repo; set before the lookup so it can carry one
        self.token = ""
        self.token_focus = False
        self._rel_box = {"done": False}
        self._start_release_check()
        # install path the user can edit
        self.path = appinstall.default_install_dir()
        self.path_focus = False
        self.launch = True
        # folder browser (None until Browse is pressed)
        self.browse = None            # {"dir": str} while open
        # install worker
        self.inst_box = None
        self.status = ""
        self.done = False

    # -------------------------------------------------------------- geometry

    def _layout(self):
        w, h = self.size
        self.title = pygame.Rect(0, 0, w, self.caption_h)
        lx, top = 18, self.caption_h + 16
        self.info_rect = pygame.Rect(lx, top, w - 2 * lx, 54)
        top += 66
        self.path_label = pygame.Rect(lx, top, w - 2 * lx, 16)
        top += 20
        self.path_field = pygame.Rect(lx, top, w - 2 * lx - 96, 22)
        self.browse_btn = pygame.Rect(self.path_field.right + 6, top, 90, 22)
        top += 30
        self.launch_box = pygame.Rect(lx, top, 220, 16)
        top += 30
        self.token_label = pygame.Rect(lx, top, 240, 16)
        top += 20
        self.token_field = pygame.Rect(lx, top, w - 2 * lx - 96, 22)
        self.token_help = pygame.Rect(self.token_field.right + 6, top, 24, 22)
        top = h - 58
        self.install_btn = pygame.Rect(w - 2 * lx - 180, top, 90, 26)
        self.quit_btn = pygame.Rect(w - lx - 90, top, 90, 26)
        self.status_rect = pygame.Rect(lx, h - 26, w - 2 * lx, 18)

    def _load_logo(self):
        """The app logo from the package's assets folder, in the two sizes
        the chrome needs. The path is package-relative, so it also resolves
        inside a frozen bundle (where _ROOT would point at a temp dir)."""
        png = const.ASSETS_DIR / "logo.png"
        try:
            img = pygame.image.load(str(png)).convert_alpha()
            return (pygame.transform.smoothscale(img, (32, 32)),
                    pygame.transform.smoothscale(img, (16, 16)))
        except (pygame.error, OSError):
            return None, None

    def _sdlwin(self, create=False):
        """The one live SDL window wrapper, reused for the session; None when
        SDL2 helpers are unavailable (e.g. the headless dummy driver)."""
        if _sdl2 is None:
            return None
        if self._win is None and create:
            try:
                self._win = _sdl2.Window.from_display_module()
            except Exception:
                return None
        return self._win

    def _video_driver(self):
        """SDL's name for the video backend in use ("x11", "wayland",
        "windows", "cocoa", "dummy"), or "" when it will not say. pygame is
        asked first - it always knows which backend it opened - and the ctypes
        read is the back-up for a build that cannot reach its own SDL library."""
        try:
            name = pygame.display.get_driver() or ""
        except (pygame.error, AttributeError):
            name = ""
        if name:
            return name
        if self._dll is None:
            return ""
        try:
            fn = self._dll.SDL_GetCurrentVideoDriver
            fn.restype = ctypes.c_char_p
            return (fn() or b"").decode("utf-8", "replace")
        except Exception:
            return ""

    @property
    def caption_h(self):
        """Height of the caption strip the installer paints: nothing where
        the window keeps the system's own titlebar."""
        return TITLEBAR_H if getattr(self, "_movable", True) else 0

    def _apply_icon_and_chrome(self):
        """Give the window the FligChess logo icon and drop the native
        titlebar, so the installer looks like the main app: one drawn Luna
        caption, the FligChess icon in the taskbar. Where this app cannot
        place its own window (Wayland) the native one stays on, since it is
        the only caption that can be dragged there. Both degrade to nothing
        when SDL cannot help (headless), never crash."""
        if self.logo is not None:
            try:
                pygame.display.set_icon(self.logo)
            except pygame.error:
                pass
        win = self._sdlwin(create=True)
        if win is not None:
            try:
                win.borderless = self._movable
            except Exception:
                pass

    def _global_mouse(self):
        """Desktop-wide cursor position, or None when SDL cannot report it
        (headless dummy driver, Wayland, no library). The only reference that
        stays put while the borderless window moves underneath the cursor."""
        if self._dll is None:
            return None
        try:
            fn = self._dll.SDL_GetGlobalMouseState
            fn.argtypes = [ctypes.POINTER(ctypes.c_int),
                           ctypes.POINTER(ctypes.c_int)]
            fn.restype = ctypes.c_int
            x, y = ctypes.c_int(), ctypes.c_int()
            if fn(ctypes.byref(x), ctypes.byref(y)) < 0:
                return None
            return (x.value, y.value)
        except Exception:
            return None

    def _drag_start(self, pos):
        """Pressing the drawn caption grabs the window for a move, and the
        thin band along the border grabs it for a resize. When SDL can report
        the desktop cursor the drag anchors on it (stable even when the
        cursor leaves the window); when it cannot - Wayland - the drag falls
        back to in-window motion, re-anchored after every applied step so
        the window's own movement never feeds back into the cursor reading.
        Returns True when the press was eaten."""
        if not self._movable:
            return False          # the system border is the handle here
        close = getattr(self, "_close_btn", None)
        if close and close.collidepoint(pos):
            return False
        edges = self._resize_edges(pos)
        kind = "resize" if edges else (
            "move" if self.title.collidepoint(pos) else None)
        if kind is None:
            return False
        win = self._sdlwin()
        if win is None:
            return True               # nothing to drag, but eat the press
        try:
            p0 = win.position
        except Exception:
            return True
        g0 = self._global_mouse()
        d = {"kind": kind, "g0": g0, "p0": p0, "s0": self.size,
             "edges": edges or ""}
        if g0 is None:
            d["base"] = tuple(p0)
            d["bsize"] = tuple(self.size)
            d["m_anchor"] = pygame.mouse.get_pos()
        self._drag = d
        return True

    def _resize_edges(self, pos):
        """Which border band the point grabs: a string of l/r/t/b."""
        if not self._movable:
            return ""
        w, h = self.size
        return "".join(n for n, on in (
            ("l", pos[0] < EDGE_GRAB), ("r", pos[0] >= w - EDGE_GRAB),
            ("t", pos[1] < EDGE_GRAB),
            ("b", pos[1] >= h - EDGE_GRAB)) if on)

    def _drag_update(self, rel):
        """During a grab, only record where the window should go; the SDL
        call happens once per frame in _apply_window_drag, outside the event
        pump (moving the window while iterating events re-enters the message
        loop and floods synthetic motion, which caused the old jitter)."""
        d = self._drag
        if not d:
            return
        if d.get("g0") is not None:
            g = self._global_mouse()
            if g is None:
                return
            dx, dy = g[0] - d["g0"][0], g[1] - d["g0"][1]
            base, bsize = d["p0"], d["s0"]
        else:
            m = pygame.mouse.get_pos()
            dx, dy = m[0] - d["m_anchor"][0], m[1] - d["m_anchor"][1]
            base, bsize = d["base"], d["bsize"]
        if d["kind"] == "resize":
            e = d["edges"]
            x, y = base
            w, h = bsize
            if "r" in e:
                w = max(MIN_WIN[0], w + dx)
            if "b" in e:
                h = max(MIN_WIN[1], h + dy)
            if "l" in e:
                right = x + w             # the far edge stays pinned
                w = max(MIN_WIN[0], w - dx)
                x = right - w
            if "t" in e:
                bottom = y + h
                h = max(MIN_WIN[1], h - dy)
                y = bottom - h
            self._win_pending = {"pos": (int(x), int(y)),
                                 "size": (int(w), int(h))}
        else:
            self._win_pending = {"pos": (int(base[0] + dx),
                                         int(base[1] + dy))}

    def _apply_window_drag(self):
        """Move/resize the borderless window once per frame, after the pump.
        In the relative-motion fallback the anchors follow whatever was
        actually applied, so synthetic motion from the move cancels itself."""
        p = self._win_pending
        if not p:
            return
        self._win_pending = None
        win = self._sdlwin()
        if win is None:
            return
        pos, size = p.get("pos"), p.get("size")
        try:
            if size:
                win.size = size
            if pos:
                win.position = pos
        except Exception:
            self._drag = None
            return
        d = self._drag
        if d is not None and d.get("g0") is None:
            if pos:
                d["base"] = pos
            if size:
                d["bsize"] = size
            d["m_anchor"] = pygame.mouse.get_pos()

    # ------------------------------------------------------- release lookup

    def _start_release_check(self):
        """Ask GitHub for the newest release on a worker thread. The token
        typed in the field (if any) rides along and beats config/env."""
        box = {"done": False}
        self._rel_box = box
        token = self.token.strip()
        self._rel_token = token

        def work():
            try:
                box["release"] = releases.latest_release(token=token or None)
            except Exception as exc:  # noqa: BLE001 - show, never crash
                box["error"] = str(exc)
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()

    def _maybe_recheck(self):
        """A token typed after a failed lookup deserves a second try - the
        anonymous rate limit is exactly the case the field exists for."""
        if self.release is not None or not self._rel_box.get("done"):
            return
        if self.token.strip() != self._rel_token:
            self.release_err = None
            self._start_release_check()

    def _poll_release(self):
        box = self._rel_box
        if box is None or not box.get("done") or self.release is not None:
            return
        if box.get("error"):
            self.release_err = box["error"]
        else:
            self.release = box["release"]

    # -------------------------------------------------------------- install

    def _start_install(self):
        if self.release is None or self.inst_box is not None:
            return
        box = {"done": False, "error": None, "exe": None,
               "progress": 0.0}
        dest = self.path
        release = self.release
        token = self.token.strip()

        def report(done, total):
            box["progress"] = (done / total) if total else 0.0

        def work():
            try:
                box["exe"] = appinstall.install_release(release, dest,
                                                        progress=report,
                                                        token=token or None)
            except Exception as exc:  # noqa: BLE001 - show, never crash
                box["error"] = str(exc)
            finally:
                box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self.inst_box = box
        self.status = "Downloading..."

    def _poll_install(self):
        box = self.inst_box
        if box is None or not box.get("done"):
            if box is not None:
                pct = int(box["progress"] * 100)
                self.status = f"Downloading... {pct}%"
            return
        self.inst_box = None
        if box.get("error"):
            self.status = f"Install failed: {box['error']}"
            return
        exe = box["exe"]
        if self.token.strip():
            self._store_token_in_config()
        icon = os.path.join(self.path, "logo.ico") if os.name == "nt" else None
        appinstall.desktop_shortcut(exe, icon)
        if self.launch:
            try:
                flags = getattr(subprocess, "DETACHED_PROCESS", 0)
                subprocess.Popen([exe], cwd=os.path.dirname(exe),
                                 close_fds=True, creationflags=flags)
            except OSError:
                pass
        self.status = f"FligChess installed to {self.path}"
        self.done = True

    def _store_token_in_config(self):
        """Drop the token into the install folder's config/updater.json so
        the app's own bot updater and \"Update client\" use it too; any
        existing keys (repo, auto_check) are preserved. Best effort: an
        unwritable folder must never fail an otherwise good install."""
        try:
            folder = os.path.join(self.path, "config")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "updater.json")
            data = {}
            try:
                with open(path, encoding="utf-8") as fh:
                    got = json.load(fh)
                if isinstance(got, dict):
                    data = got
            except (OSError, ValueError):
                pass
            data["token"] = self.token.strip()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError:
            pass

    # ---------------------------------------------------------- folder browse

    def _open_browser(self):
        start = self.path if os.path.isdir(self.path) else os.path.expanduser(
            "~")
        self.browse = {"dir": None, "entries": [], "scroll": 0}
        self._browse_go(os.path.abspath(start))

    def _browse_go(self, path):
        """Descend into a folder: the listing is read once per step (not per
        frame) and the scroll resets to the top."""
        self.browse["dir"] = path
        self.browse["entries"] = self._browse_entries()
        self.browse["scroll"] = 0

    def _browse_max_scroll(self):
        return max(0, len(self.browse["entries"]) - getattr(
            self, "_b_rows_n", 1))

    def _browse_scroll_by(self, rows):
        self.browse["scroll"] = max(0, min(self._browse_max_scroll(),
                                            self.browse["scroll"] + rows))

    def _browse_entries(self):
        try:
            out = []
            for name in sorted(os.listdir(self.browse["dir"])):
                full = os.path.join(self.browse["dir"], name)
                if os.path.isdir(full) and not name.startswith("."):
                    out.append(name)
            return out
        except OSError:
            return []

    # --------------------------------------------------------------- drawing

    def draw(self):
        self.screen.fill(xp.FACE)
        # no caption is drawn where the window wears the system's own, and
        # with it no close box: the native one handles both jobs
        self._close_btn = xp.titlebar(self.screen, self.title,
                                      "Install FligChess", self.font,
                                      icon=self.icon) if self.caption_h else None
        self._draw_release()
        self._draw_main()
        if self.browse is not None:
            self._draw_browser()
        else:
            self._draw_tooltip()
        pygame.display.flip()

    def _draw_tooltip(self):
        """The tool tip explaining the optional token, shown while the
        pointer rests on the \"?\" or the field's label."""
        mx, my = pygame.mouse.get_pos()
        if (self.token_help.collidepoint(mx, my)
                or self.token_label.collidepoint(mx, my)):
            xp.tooltip(self.screen, (mx, my), TOKEN_TIP, self.small)

    def _draw_release(self):
        r = self.info_rect
        xp.groupbox(self.screen, r.inflate(-2, -4), "", self.small)
        pygame.draw.rect(self.screen, xp.HILITE, r.inflate(2, 4), 1)
        pygame.draw.rect(self.screen, xp.SHADOW, r, 1)
        if self.release_err:
            head = "FligChess"
            sub = f"Could not reach the release list: {self.release_err}"
        elif self.release is None:
            head = "FligChess"
            sub = "Checking for the newest release..."
        else:
            head = f"FligChess {self.release['version']}"
            sub = f"Released {self.release['published_at'][:10]}"
        self.screen.blit(self.big.render(head, True, xp.TEXT),
                         (r.x + 12, r.y + 6))
        self.screen.blit(self.small.render(sub, True, xp.TEXT_MUTED),
                         (r.x + 12, r.y + 30))

    def _draw_main(self):
        self.screen.blit(self.font.render("Install folder:", True, xp.TEXT),
                         (self.path_label.x, self.path_label.y))
        xp.field(self.screen, self.path_field)
        text = self.path
        shown = self._tail(text, self.path_field.width - 10, self.font)
        self.screen.blit(self.font.render(shown, True, xp.TEXT),
                         (self.path_field.x + 4,
                          self.path_field.centery - 8))
        if self.path_focus:
            pygame.draw.rect(self.screen, (20, 90, 200),
                             self.path_field, 1)
        mx, my = pygame.mouse.get_pos()
        xp.button(self.screen, self.browse_btn, "Browse...", self.font,
                  hovered=self.browse_btn.collidepoint(mx, my),
                  enabled=self.release is not None)
        xp.checkbox(self.screen, (self.launch_box.x,
                                  self.launch_box.centery), self.launch,
                    "Launch when done", self.font)
        # optional GitHub token, masked like a password so a shoulder can't
        # read it; the "?" carries the explanation as a tool tip
        self.screen.blit(self.font.render(
            "GitHub token (optional):", True, xp.TEXT),
            (self.token_label.x, self.token_label.y))
        xp.field(self.screen, self.token_field)
        dots = "\u2022" * len(self.token)
        self.screen.blit(self.font.render(dots, True, xp.TEXT),
                         (self.token_field.x + 4,
                          self.token_field.centery - 8))
        if self.token_focus:
            pygame.draw.rect(self.screen, (20, 90, 200),
                             self.token_field, 1)
            tail = self.font.render("|", True, xp.TEXT)
            self.screen.blit(tail, (min(self.token_field.x + 6
                                        + self.font.size(dots)[0],
                                        self.token_field.right - 6),
                                     self.token_field.centery - 8))
        xp.button(self.screen, self.token_help, "?", self.font,
                  hovered=self.token_help.collidepoint(mx, my))
        can_install = self.release is not None and self.inst_box is None \
            and not self.done
        xp.button(self.screen, self.install_btn, "Install", self.font,
                  default=True, hovered=self.install_btn.collidepoint(mx, my),
                  enabled=can_install)
        xp.button(self.screen, self.quit_btn, "Quit" if not self.done
                  else "Close", self.font,
                  hovered=self.quit_btn.collidepoint(mx, my))
        if self.status:
            self.screen.blit(self.small.render(
            self._tail(self.status, self.status_rect.width, self.small), True,
                xp.TEXT_MUTED), (self.status_rect.x, self.status_rect.y))

    def _draw_browser(self):
        """The folder dialog: it grows with the window (a default frame
        already shows a dozen entries) and a classic scrollbar takes over
        when the folder holds more than fit."""
        w, h = self.size
        bw = min(max(w - 60, 340), 560)
        bh = min(max(h - 60, 260), 480)
        box = pygame.Rect((w - bw) // 2, (h - bh) // 2, bw, bh)
        body, _close = xp.message_frame(self.screen, box, "Choose a folder",
                                        self.font, icon=self.icon)
        self._b_close = _close
        xp.groupbox(self.screen, body.inflate(-6, -6), "", self.small)
        # the buttons ride the dialog's bottom, the list fills between
        self._b_up = pygame.Rect(body.x + 6, body.bottom - 30, 60, 24)
        self._b_ok = pygame.Rect(body.right - 102, body.bottom - 30, 96, 24)
        self._b_cancel = pygame.Rect(self._b_ok.x - 72, body.bottom - 30,
                                     66, 24)
        head = pygame.Rect(body.x + 8, body.y + 6, body.width - 16, 18)
        lst = pygame.Rect(body.x + 8, head.bottom + 4, body.width - 16,
                          body.bottom - 38 - head.bottom - 8)
        self.screen.blit(self.small.render(
            self._tail(self.browse["dir"], head.width, self.small), True,
            xp.TEXT),
            (head.x, head.y))
        xp.field(self.screen, lst)
        view = pygame.Rect(lst.x + 2, lst.y + 2, lst.width - 4, lst.height - 4)
        row_h = 18
        entries = self.browse["entries"]
        total = len(entries)
        self._b_rows_n = max(1, view.height // row_h)
        self.browse["scroll"] = min(self.browse["scroll"],
                                    self._browse_max_scroll())
        scroll = self.browse["scroll"]
        if total > self._b_rows_n:
            track = pygame.Rect(view.right - 14, view.y, 14, view.height)
        else:
            track = pygame.Rect(view.right, view.y, 0, view.height)
        self._b_track = track
        self._browse_rows = []
        for i in range(scroll, min(total, scroll + self._b_rows_n + 1)):
            ry = view.y + (i - scroll) * row_h
            if ry + row_h > view.bottom:
                break
            row = pygame.Rect(view.x, ry, track.x - view.x - 6, row_h)
            self._browse_rows.append((entries[i], row))
            self._draw_folder(row.x + 3, row.centery)
            self.screen.blit(self.font.render(
                self._tail(entries[i], row.width - 24, self.font), True,
                xp.TEXT), (row.x + 22, ry + 1))
        thumb = None
        if total > self._b_rows_n:
            pygame.draw.rect(self.screen, (242, 240, 232), track)
            pygame.draw.rect(self.screen, xp.SHADOW, track, 1)
            th = max(24, int(track.height * self._b_rows_n / total))
            span = max(1, track.height - th)
            ty = track.y + int(span * scroll
                               / max(1, self._browse_max_scroll()))
            thumb = pygame.Rect(track.x + 1, ty, track.width - 2, th)
            xp.gradient_v(self.screen, thumb, xp.BTN_TOP, xp.BTN_BOT)
            pygame.draw.rect(self.screen, xp.BTN_BORDER, thumb, 1)
            pygame.draw.line(self.screen, xp.SHADOW,
                             (thumb.centerx - 2, thumb.centery),
                             (thumb.centerx + 2, thumb.centery))
            pygame.draw.line(self.screen, xp.HILITE,
                             (thumb.centerx - 2, thumb.centery + 1),
                             (thumb.centerx + 2, thumb.centery + 1))
        self._b_thumb = thumb
        mx, my = pygame.mouse.get_pos()
        xp.button(self.screen, self._b_up, "Up", self.font,
                  hovered=self._b_up.collidepoint(mx, my))
        xp.button(self.screen, self._b_ok, "Choose", self.font, default=True,
                  hovered=self._b_ok.collidepoint(mx, my))
        xp.button(self.screen, self._b_cancel, "Cancel", self.font,
                  hovered=self._b_cancel.collidepoint(mx, my))

    def _draw_folder(self, x, cy):
        """The little manila folder mark in front of every entry."""
        r = pygame.Rect(x, cy - 4, 12, 9)
        pygame.draw.rect(self.screen, (224, 180, 92), r)
        pygame.draw.rect(self.screen, (160, 122, 58), r, 1)
        pygame.draw.rect(self.screen, (224, 180, 92), (r.x + 1, r.y - 2, 5, 2))

    def _sb_scroll_to(self, my):
        """During a scrollbar drag the thumb follows the cursor: map its
        travel range onto the row range."""
        track, thumb = getattr(self, "_b_track", None), self._b_thumb
        if not track or thumb is None:
            return
        span = max(1, track.height - thumb.height)
        frac = (my - track.y - thumb.height // 2) / span
        self.browse["scroll"] = int(round(max(0.0, min(1.0, frac))
                                          * self._browse_max_scroll()))

    def _tail(self, text, width, font=None):
        """Right-align long paths by keeping the tail, trimming the front so
        the text still fits its box."""
        font = font or self.font
        text = text or ""
        while text and font.size(text)[0] > width:
            text = text[1:]
        return text

    # ------------------------------------------------------------- main loop

    def handle(self, event):
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type == pygame.VIDEORESIZE:
            # the surface already changed; clamp only what we lay out, so
            # a drag below the minimum keeps the widgets usable
            self.size = (max(event.w, MIN_WIN[0]), max(event.h, MIN_WIN[1]))
            self._layout()
            return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._drag = None
            self._win_pending = None
            self._b_sbdrag = False
        if self.browse is not None:
            self._handle_browse(event)
            return
        if event.type == pygame.MOUSEMOTION and self._drag:
            self._drag_update(event.rel)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if not self._drag_start(event.pos):
                self._click(event.pos)
        elif event.type == pygame.TEXTINPUT and self.path_focus:
            if event.text in ("\b", "\x7f"):   # platforms carrying it here
                self.path = self.path[:-1]
            elif event.text.isprintable():
                self.path += event.text
        elif event.type == pygame.TEXTINPUT and self.token_focus:
            if event.text in ("\b", "\x7f"):   # platforms carrying it here
                self.token = self.token[:-1]
            else:
                clean = "".join(c for c in event.text if not c.isspace())
                self.token = (self.token + clean)[:255]
        elif event.type == pygame.KEYDOWN and self.token_focus:
            if event.key == pygame.K_BACKSPACE:
                self.token = self.token[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                               pygame.K_ESCAPE):
                # leaving the field commits the token; Enter means "look
                # up the release again with whatever I just typed"
                self.token_focus = False
                self._maybe_recheck()
        elif event.type == pygame.KEYDOWN and self.path_focus:
            if event.key == pygame.K_BACKSPACE:
                self.path = self.path[:-1]
            elif event.key == pygame.K_RETURN:
                self.path_focus = False

    def _click(self, pos):
        was_token = self.token_focus
        if self._close_btn and self._close_btn.collidepoint(pos):
            self.running = False
        elif self.browse_btn.collidepoint(pos) and self.release is not None:
            self._open_browser()
        elif self.path_field.collidepoint(pos):
            self.path_focus = True
            self.token_focus = False
        elif self.token_field.collidepoint(pos):
            self.token_focus = True
            self.path_focus = False
        elif self.launch_box.collidepoint(pos):
            self.launch = not self.launch
        elif self.install_btn.collidepoint(pos):
            self._start_install()
        elif self.quit_btn.collidepoint(pos):
            self.running = False
        else:
            self.path_focus = False
            self.token_focus = False
        if was_token and not self.token_focus:
            self._maybe_recheck()      # clicking away commits a typed token

    def _handle_browse(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.browse = None
            return
        if event.type == pygame.MOUSEWHEEL:
            self._browse_scroll_by(-3 if event.y > 0 else 3)
            return
        if event.type == pygame.MOUSEMOTION:
            if getattr(self, "_b_sbdrag", False):
                self._sb_scroll_to(event.pos[1])
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        pos = event.pos
        if getattr(self, "_b_close", None) and self._b_close.collidepoint(pos):
            self.browse = None
        elif self._b_cancel.collidepoint(pos):
            self.browse = None
        elif self._b_ok.collidepoint(pos):
            self.path = self.browse["dir"]
            self.browse = None
        elif self._b_up.collidepoint(pos):
            parent = os.path.dirname(self.browse["dir"])
            if parent and parent != self.browse["dir"]:
                self._browse_go(parent)
        elif (getattr(self, "_b_thumb", None)
                and self._b_thumb.collidepoint(pos)):
            self._b_sbdrag = True          # grab the thumb where it lies
        elif self._b_track.collidepoint(pos) and self._b_thumb:
            # a click in the trough pages toward the click, and starts a
            # drag so holding the button keeps scrolling
            self._browse_scroll_by(-self._b_rows_n
                                   if pos[1] < self._b_thumb.y
                                   else self._b_rows_n)
            self._b_sbdrag = True
        else:
            for name, row in getattr(self, "_browse_rows", []):
                if row.collidepoint(pos):
                    self._browse_go(os.path.join(self.browse["dir"], name))
                    break

    def run(self):
        while self.running:
            for event in pygame.event.get():
                self.handle(event)
            # the window moves here, once per frame, not while events are
            # being iterated: repositioning mid-pump re-enters SDL's message
            # loop and floods the queue with synthetic motion (the jitter)
            self._apply_window_drag()
            self._poll_release()
            self._poll_install()
            self.draw()
            self.clock.tick(30)
        pygame.quit()


def _error_log_paths():
    """Where a fatal failure is written down, best place first: beside the
    executable in a bundled build, beside this script in a checkout, and in
    the home folder when the program's own folder will not take a file. A
    crash on somebody else's machine has to leave something behind to read."""
    base = (pathlib.Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else pathlib.Path(__file__).resolve().parent)
    out = [base / "FligChess-Installer-error.txt"]
    try:
        out.append(pathlib.Path.home() / "FligChess-Installer-error.txt")
    except RuntimeError:
        pass                                  # no home directory to fall back on
    return out


def _hold_open():
    """Keep a crash note on screen until it has been seen: one click, a key,
    a close request, or two minutes - whichever comes first. A window that
    opens and vanishes in a blink tells the user nothing."""
    clock = pygame.time.Clock()
    t0 = time.time()
    while time.time() - t0 < 120:
        for event in pygame.event.get():
            if event.type in (pygame.QUIT, pygame.MOUSEBUTTONDOWN,
                              pygame.KEYDOWN):
                return
        clock.tick(20)


def _report_crash():
    """Make a failure legible instead of silent: the traceback goes to the
    console and into a file, and where a window did open its last line and
    the file's address are painted there and held. Never fatal itself - the
    original error is what the caller still exits on."""
    text = traceback.format_exc()
    sys.stderr.write(text + "\n")
    where = None
    for log in _error_log_paths():
        try:
            log.write_text("FligChess installer %s\n\n%s" % (APP_VERSION,
                                                              text),
                           encoding="utf-8")
            where = str(log)
            break
        except OSError:
            continue
    if where is None:
        where = "no log file could be written"
    try:
        screen = pygame.display.get_surface()
        if screen is None:
            return
        small = pygame.font.SysFont(UI_FONT, 12)
        big = pygame.font.SysFont(UI_FONT, 15, bold=True)
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        screen.fill(xp.FACE)
        screen.blit(big.render("FligChess installer stopped unexpectedly",
                               True, xp.TEXT), (16, 14))
        screen.blit(small.render((lines[-1] if lines else "?")[:120], True,
                                 xp.TEXT), (16, 44))
        screen.blit(small.render("Details: " + where, True, xp.TEXT_MUTED),
                    (16, 64))
        pygame.display.flip()
        _hold_open()
    except Exception:
        pass


def main(argv=None):
    """--selftest is the build workflow's probe, and opens no window: it
    reports the interpreter, pygame and the bundled asset folder the frozen
    bundle actually got, and fails if an asset is missing. A bundle that
    cannot start then fails the build instead of somebody's desktop - which
    is the only thing that can be done about a crash during interpreter
    startup, since that is before any of this file exists."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--selftest" in argv:
        logo = const.ASSETS_DIR / "logo.png"
        print("FligChess installer %s\n  python %s\n  pygame %s\n  assets %s"
              % (APP_VERSION, sys.version.split()[0], pygame.version.ver,
                 const.ASSETS_DIR))
        if not logo.exists():
            print("  missing bundled asset: %s" % logo)
            raise SystemExit(2)
        return
    try:
        Installer().run()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:                # a crash must never leave no trace
        _report_crash()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
