import os
import sys
import pathlib
sys.path.insert(0, "src")
sys.path.insert(0, "installer")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
import installer as I

log = pathlib.Path(I.__file__).resolve().parent / "FligChess-Installer-error.txt"
log.unlink(missing_ok=True)

# a window exists (as after a mid-startup failure), and a click is already
# queued so the held-open note returns at once
pygame.init()
pygame.display.set_mode((400, 200))
pygame.event.post(pygame.event.Event(pygame.QUIT))


def boom():
    raise ValueError("Unable to open a connection to the display")


I.Installer.run = boom
try:
    I.main()
except SystemExit as exc:
    print("exit code:", exc.code)
print("log written:", log.exists())
print(log.read_text(encoding="utf-8").splitlines()[-1])
log.unlink(missing_ok=True)
print("DONE")
