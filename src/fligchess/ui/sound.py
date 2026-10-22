"""Synthesized sound effects: no asset files, no extra dependencies.

Each effect is a short PCM buffer built from sine notes or a noise burst the
first time it is asked for. Anything audio-related that can fail - no mixer,
no audio device, an odd sample format, the dummy driver the headless tests
run under - degrades to silence instead of an error, because a chess app
without sound is still a chess app.

The effects are built at whatever sample rate the mixer actually opened, so
the same code works on a 44.1 kHz desktop and a 22 kHz laptop. `Sfx.play` is
safe to call from the draw and update loop since nothing here blocks.
"""

import array
import math
import random

import pygame

# Settings > volume, as a factor applied to every effect.
VOLUME = {"quiet": 0.3, "normal": 0.65, "loud": 1.0}

# name -> the notes it plays in order, as (frequency, seconds); a frequency
# of 0 is a decaying noise burst, which is what an explosion needs and no
# sine wave can be
TONES = {
    "click": ((880, 0.025),),
    "select": ((660, 0.035),),
    "move": ((392, 0.04), (294, 0.05)),
    "capture": ((523, 0.03), (0, 0.08)),
    "blast": ((0, 0.22),),
    "check": ((523, 0.05), (784, 0.05), (1046, 0.07)),
    "end": ((523, 0.1), (415, 0.1), (330, 0.18)),
}


def _note(freq, seconds, rate):
    """One struck sine note: quick attack, exponential decay to silence."""
    count = max(1, int(seconds * rate))
    peak = 0.38 * 32767
    out = []
    for i in range(count):
        t = i / rate
        envelope = math.exp(-6.0 * i / count)
        value = peak * envelope * math.sin(2 * math.pi * freq * t)
        out.append(int(max(-32768, min(32767, value))))
    return out


def _noise(seconds, rate):
    """A low rumble that decays: filtered random samples, no sine in it."""
    count = max(1, int(seconds * rate))
    peak = 0.5 * 32767
    out = []
    last = 0.0
    for i in range(count):
        last = 0.7 * last + 0.3 * random.uniform(-1.0, 1.0)
        value = peak * math.exp(-4.0 * i / count) * last
        out.append(int(max(-32768, min(32767, value))))
    return out


def build(name, rate, channels):
    """Raw 16 bit PCM bytes of one effect, at the mixer's own format."""
    if name not in TONES:
        raise ValueError(f"no such sound: {name}")
    mono = []
    for freq, seconds in TONES[name]:
        mono += (_noise(seconds, rate) if freq == 0
                 else _note(freq, seconds, rate))
    if channels > 1:              # SDL wants the same sample on every channel
        mono = [value for value in mono for _ in range(channels)]
    return array.array("h", mono).tobytes()


class Sfx:
    """Plays the effects in TONES; every failure on the way is silence."""

    def __init__(self):
        self.enabled = True
        self.volume = VOLUME["normal"]
        self._sounds = {}
        self.format = self._open()

    @staticmethod
    def _open():
        """(rate, channels) of a usable mixer, or None when there is none."""
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            init = pygame.mixer.get_init()
        except pygame.error:
            return None
        if not init:
            return None
        rate, size, channels = init
        return (rate, channels) if size == -16 else None

    def set_enabled(self, on):
        self.enabled = bool(on)

    def set_volume(self, level):
        """Settings > volume: "quiet", "normal" or "loud"."""
        self.volume = VOLUME.get(level, VOLUME["normal"])

    def play(self, name):
        """Fire an effect; unknown names and broken audio are ignored."""
        if not self.enabled or self.format is None or name not in TONES:
            return
        sound = self._sounds.get(name)
        if sound is None:
            try:
                sound = pygame.mixer.Sound(
                    buffer=build(name, *self.format))
            except (pygame.error, ValueError):
                self.format = None          # stop trying, there is no audio
                return
            sound.set_volume(self.volume)
            self._sounds[name] = sound
        else:
            sound.set_volume(self.volume)   # volume may have changed since
        try:
            sound.play()
        except pygame.error:
            self.format = None
