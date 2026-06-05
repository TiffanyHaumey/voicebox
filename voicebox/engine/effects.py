import numpy as np
from voicebox.constants import SAMPLE_RATE

class Effect:
    """Base: callable block->block. Override process(). reset() clears state."""
    def __call__(self, block: np.ndarray) -> np.ndarray:
        return self.process(block).astype(np.float32, copy=False)
    def process(self, block: np.ndarray) -> np.ndarray:
        return block
    def reset(self) -> None:
        pass

class Gain(Effect):
    def __init__(self, gain_db: float = 0.0):
        self.k = float(10 ** (gain_db / 20))
    def process(self, block):
        return block * self.k

class RingMod(Effect):
    def __init__(self, freq: float = 200.0):
        self.freq = float(freq)
        self.phase = 0.0
    def process(self, block):
        n = len(block)
        t = (self.phase + np.arange(n)) / SAMPLE_RATE
        self.phase += n
        return block * np.sin(2 * np.pi * self.freq * t).astype(np.float32)
    def reset(self):
        self.phase = 0.0

class Bitcrush(Effect):
    def __init__(self, bits: int = 8):
        self.levels = float(2 ** int(bits))
    def process(self, block):
        return np.round(block * self.levels) / self.levels

class Distortion(Effect):
    def __init__(self, drive: float = 2.0):
        self.drive = float(drive)
    def process(self, block):
        return np.tanh(block * self.drive)
