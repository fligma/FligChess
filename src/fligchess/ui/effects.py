"""Capture effects: every piece type "dies" differently when taken.

Each effect is spawned at the square where the capture happened and plays
out over a fraction of a second. Effects only draw into the given surface
at a pixel offset, so the app can keep rendering the board underneath.
`Blast` is the extra one the minefield variant uses.
"""

import math
import random

import pygame


class CaptureEffect:
    """Base class: tracks elapsed time and reports when finished."""

    duration = 0.5

    def __init__(self, sprite):
        self.sprite = sprite
        self.t = 0.0

    @property
    def done(self):
        return self.t >= self.duration

    def update(self, dt):
        self.t = min(self.t + dt, self.duration)
        self.step(dt)

    def step(self, dt):
        pass

    def draw(self, target, x, y):
        pass

    def _fade(self, tail=0.4):
        """Alpha multiplier: 1 until the last `tail` fraction of the life."""
        cutoff = self.duration * (1 - tail)
        if self.t < cutoff:
            return 1.0
        return max(0.0, 1.0 - (self.t - cutoff) / (self.duration * tail))


def _split_chunks(sprite, rows=4, cols=4):
    """Cut a sprite into a grid of pieces. Returns [(surface, x, y, row)]."""
    w, h = sprite.get_size()
    cw, ch = w / cols, h / rows
    chunks = []
    for row in range(rows):
        for col in range(cols):
            x, y = int(col * cw), int(row * ch)
            sub = sprite.subsurface(x, y, min(int(cw), w - x), min(int(ch), h - y)).copy()
            chunks.append({"s": sub, "x": float(x), "y": float(y),
                           "vx": 0.0, "vy": 0.0, "row": row})
    return chunks


class Crumble(CaptureEffect):
    """Pawn: breaks into little squares that scatter and fall away."""

    duration = 0.6

    def __init__(self, sprite):
        super().__init__(sprite)
        self.chunks = _split_chunks(sprite)
        for c in self.chunks:
            c["vx"] = random.uniform(-45, 45)
            c["vy"] = random.uniform(-70, -10)

    def step(self, dt):
        for c in self.chunks:
            c["vy"] += 520 * dt
            c["x"] += c["vx"] * dt
            c["y"] += c["vy"] * dt

    def draw(self, target, x, y):
        alpha = int(255 * self._fade())
        for c in self.chunks:
            c["s"].set_alpha(alpha)
            target.blit(c["s"], (int(x + c["x"]), int(y + c["y"])))


class Collapse(CaptureEffect):
    """Rook: top rows lose their support first and the tower falls down."""

    duration = 0.55

    def __init__(self, sprite):
        super().__init__(sprite)
        self.chunks = _split_chunks(sprite, rows=5)
        for c in self.chunks:
            c["delay"] = c["row"] * 0.07  # higher rows (lower index) go first
            c["vx"] = random.uniform(-12, 12)

    def step(self, dt):
        for c in self.chunks:
            if self.t <= c["delay"]:
                continue
            c["vy"] += 900 * dt
            c["x"] += c["vx"] * dt
            c["y"] += c["vy"] * dt

    def draw(self, target, x, y):
        alpha = int(255 * self._fade())
        for c in self.chunks:
            c["s"].set_alpha(alpha)
            target.blit(c["s"], (int(x + c["x"]), int(y + c["y"])))


class SpinOff(CaptureEffect):
    """Knight: rears up spinning and is flung off the board."""

    duration = 0.5

    def __init__(self, sprite):
        super().__init__(sprite)
        self.angle = 0.0
        self.x = 0.0
        self.y = 0.0
        self.vx = 170.0
        self.vy = -230.0

    def step(self, dt):
        self.angle += 560 * dt
        self.vy += 340 * dt
        self.x += self.vx * dt
        self.y += self.vy * dt

    def draw(self, target, x, y):
        fade = self._fade(tail=0.5)
        rotated = pygame.transform.rotate(self.sprite, self.angle)
        rotated.set_alpha(int(255 * fade))
        w, h = rotated.get_size()
        sw, sh = self.sprite.get_size()
        cx, cy = x + sw / 2 + self.x, y + sh / 2 + self.y
        target.blit(rotated, (int(cx - w / 2), int(cy - h / 2)))


class Ascend(CaptureEffect):
    """Bishop: floats heavenward, fading out, leaving a few sparkles."""

    duration = 0.65

    def __init__(self, sprite):
        super().__init__(sprite)
        self.dy = 0.0
        w, h = sprite.get_size()
        self.sparkles = [(random.uniform(0, w), random.uniform(0, h),
                          random.uniform(0.05, 0.5))
                         for _ in range(8)]

    def step(self, dt):
        self.dy -= 60 * dt

    def draw(self, target, x, y):
        fade = max(0.0, 1.0 - self.t / self.duration)
        ghost = self.sprite.copy()
        ghost.set_alpha(int(255 * fade))
        target.blit(ghost, (int(x), int(y + self.dy)))
        for sx, sy, born in self.sparkles:
            if self.t >= born:
                progress = (self.t - born) / (self.duration - born + 1e-6)
                r = max(1, int(3 * (1 - progress)))
                px, py = int(x + sx), int(y + sy + self.dy * 0.6)
                pygame.draw.circle(target, (255, 255, 210), (px, py), r)


class Starburst(CaptureEffect):
    """Queen: a bright flash and a radial burst of golden particles."""

    duration = 0.55

    def __init__(self, sprite):
        super().__init__(sprite)
        w, h = sprite.get_size()
        self.cx, self.cy = w / 2, h / 2
        self.particles = []
        for _ in range(16):
            ang = random.uniform(0, math.tau)
            speed = random.uniform(130, 280)
            self.particles.append({
                "x": self.cx, "y": self.cy,
                "vx": math.cos(ang) * speed, "vy": math.sin(ang) * speed,
                "gold": random.random() < 0.6,
            })

    def step(self, dt):
        for p in self.particles:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vx"] *= 0.92
            p["vy"] *= 0.92

    def draw(self, target, x, y):
        # the sprite itself flares and dissolves quickly
        flare = max(0.0, 1.0 - self.t / (self.duration * 0.35))
        if flare > 0:
            ghost = self.sprite.copy()
            ghost.set_alpha(int(255 * flare))
            size = int(self.sprite.get_width() * (1 + 0.3 * (1 - flare)))
            scaled = pygame.transform.scale(ghost, (size, size))
            target.blit(scaled, (int(x + self.cx - size / 2),
                                 int(y + self.cy - size / 2)))
        # white flash ring right at the start
        if self.t < 0.15:
            r = int(6 + 34 * self.t / 0.15)
            pygame.draw.circle(target, (255, 255, 230),
                               (int(x + self.cx), int(y + self.cy)), r, 3)
        for p in self.particles:
            fade = max(0.0, 1.0 - self.t / self.duration)
            color = (255, 215, 90) if p["gold"] else (255, 255, 255)
            if fade > 0.2:
                pygame.draw.circle(target, color,
                                   (int(x + p["x"]), int(y + p["y"])),
                                   max(1, int(3 * fade)))


class Shockwave(CaptureEffect):
    """King: shakes, sinks down, and releases a heavy double ring."""

    duration = 0.7

    def __init__(self, sprite):
        super().__init__(sprite)
        w, h = sprite.get_size()
        self.cx, self.cy = w / 2, h / 2

    def draw(self, target, x, y):
        progress = self.t / self.duration
        # shaking, fading sprite
        fade = max(0.0, 1.0 - progress * 1.4)
        if fade > 0:
            shake = math.sin(self.t * 45) * 3 * (1 - progress)
            ghost = self.sprite.copy()
            ghost.set_alpha(int(255 * fade))
            target.blit(ghost, (int(x + shake), int(y + 6 * progress)))
        # two expanding rings
        for i, start in enumerate((0.0, 0.12)):
            if self.t <= start:
                continue
            rt = (self.t - start) / (self.duration - start)
            radius = int(8 + (self.cx + 22) * rt)
            width = max(1, int(4 * (1 - rt)))
            color = (220, 220, 220) if i == 0 else (160, 160, 160)
            if rt < 1.0:
                pygame.draw.circle(target, color,
                                   (int(x + self.cx), int(y + self.cy)),
                                   radius, width)


# Which effect each captured piece plays.
EFFECTS = {"P": Crumble, "R": Collapse, "N": SpinOff,
           "B": Ascend, "Q": Starburst, "K": Shockwave}


class Blast(CaptureEffect):
    """Minefield death: hot core, expanding fireball ring, burning debris."""

    duration = 0.7

    def __init__(self, sprite):
        super().__init__(sprite)
        w, h = sprite.get_size()
        self.cx, self.cy = w / 2, h / 2
        self.chunks = _split_chunks(sprite)
        for c in self.chunks:
            ang = math.atan2(c["y"] - self.cy, c["x"] - self.cx or 1)
            speed = random.uniform(110, 290)
            c["vx"] = math.cos(ang) * speed
            c["vy"] = math.sin(ang) * speed - 70

    def step(self, dt):
        for c in self.chunks:
            c["vy"] += 760 * dt
            c["x"] += c["vx"] * dt
            c["y"] += c["vy"] * dt

    def draw(self, target, x, y):
        center = (int(x + self.cx), int(y + self.cy))
        flare = max(0.0, 1.0 - self.t / (self.duration * 0.3))
        if flare > 0:      # white-hot core, gone in a blink
            pygame.draw.circle(target, (255, 244, 200), center,
                               int(self.cx * (0.5 + 0.7 * flare)))
        for i, start in enumerate((0.0, 0.1)):     # fireball, then smoke
            if self.t <= start:
                continue
            rt = (self.t - start) / (self.duration - start)
            if rt >= 1.0:
                continue
            color = (255, 168, 54) if i == 0 else (112, 102, 96)
            pygame.draw.circle(target, color, center,
                               int(4 + (self.cx + 16) * rt),
                               max(1, int(4 * (1 - rt))))
        alpha = int(255 * self._fade(tail=0.5))
        for c in self.chunks:
            c["s"].set_alpha(alpha)
            target.blit(c["s"], (int(x + c["x"]), int(y + c["y"])))


def make_capture_effect(piece, sprite):
    """Build the right effect for a captured piece char ('P', 'n', ...)."""
    cls = EFFECTS.get(piece.upper())
    return cls(sprite) if cls else None


def make_blast_effect(sprite):
    """The effect for a piece that trod a mine."""
    return Blast(sprite)
