import numpy as np
from scipy import signal
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

class Filter(Effect):
    def __init__(self, kind="lowpass", cutoff=1000.0, bandwidth=500.0, order=4):
        nyq = SAMPLE_RATE / 2
        if kind == "bandpass":
            lo = max(20.0, cutoff - bandwidth / 2) / nyq
            hi = min(nyq - 1, cutoff + bandwidth / 2) / nyq
            self.sos = signal.butter(order, [lo, hi], btype="bandpass", output="sos")
        else:
            self.sos = signal.butter(order, cutoff / nyq, btype=kind, output="sos")
        # zero initial state, shaped (n_sections, 2) for a 1-D (mono) signal
        self.zi = np.zeros_like(signal.sosfilt_zi(self.sos))
    def process(self, block):
        out, self.zi = signal.sosfilt(self.sos, block, zi=self.zi)
        return out
    def reset(self):
        self.zi = np.zeros_like(signal.sosfilt_zi(self.sos))

class Delay(Effect):
    # dry=1.0 -> standard echo (dry + wet); dry=0.0 -> wet-only line (used by Reverb combs)
    def __init__(self, delay_ms=200.0, feedback=0.4, wet=0.5, dry=1.0):
        self.n = max(1, int(SAMPLE_RATE * delay_ms / 1000))
        self.buf = np.zeros(self.n, np.float32)
        self.idx = 0
        self.fb = float(feedback)
        self.wet = float(wet)
        self.dry = float(dry)
    def process(self, block):
        out = np.empty_like(block)
        for i, x in enumerate(block):
            d = self.buf[self.idx]
            y = self.dry * x + self.wet * d
            self.buf[self.idx] = x + d * self.fb
            self.idx = (self.idx + 1) % self.n
            out[i] = y
        return out
    def reset(self):
        self.buf[:] = 0.0; self.idx = 0

class Reverb(Effect):
    """Lightweight Schroeder: parallel wet-only combs + series allpass, then dry/wet mix."""
    def __init__(self, room=0.5, wet=0.3):
        comb_ms = [11.0, 13.7, 15.2, 16.2]
        # dry=0.0 so each comb contributes ONLY its echo tail (no dry pass-through);
        # the dry signal is added back exactly once in process(). Avoids dry double-dip.
        self.combs = [Delay(ms, feedback=0.6 + 0.3 * room, wet=1.0, dry=0.0) for ms in comb_ms]
        self.ap = [Delay(ms, feedback=0.5, wet=1.0, dry=0.0) for ms in (5.0, 1.7)]
        self.wet = float(wet)
    def process(self, block):
        acc = np.zeros_like(block)
        for c in self.combs:
            acc += c(block)
        acc /= len(self.combs)
        for a in self.ap:
            acc = a(acc)
        out = (1 - self.wet) * block + self.wet * acc
        return np.clip(out, -1.0, 1.0)
    def reset(self):
        for d in self.combs + self.ap:
            d.reset()
