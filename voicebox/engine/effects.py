import numpy as np
from scipy import signal
from scipy.signal import resample as scipy_resample
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
        comb_ms = [29.7, 37.1, 41.1, 43.7]
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


class PitchShift(Effect):
    """Pitch shift without changing duration via windowed-OLA + resampling.

    Algorithm:
    - Accumulate input in a queue; every hop_a input samples, extract a
      win_size-sample Hann-windowed frame from the queue.
    - Resample each frame from win_size to target_len = win_size/alpha samples
      using scipy.signal.resample (squeezes the frame in time, raising pitch).
    - Overlap-add the resampled frame into an output OLA buffer at pitch-rate
      hops (hop_out = hop_a/alpha), then advance the output pointer by hop_out.
    - Per call, read hop_a samples from the output buffer (zeros during warm-up).
    - Net result: same block-rate output, pitch shifted by semitones.
    """

    def __init__(self, semitones: float = 0.0, win_size: int = 1024, hop_a: int = 256):
        self.semitones = float(semitones)
        self.win_size = int(win_size)
        self.hop_a = int(hop_a)
        # alpha > 1 pitches up; target_len < win_size = squeezes = higher pitch
        self.alpha = 2.0 ** (self.semitones / 12.0)
        self.target_len = max(1, int(round(self.win_size / self.alpha)))
        # Output hop: how far to advance synthesis position per input hop_a
        self.hop_out = max(1, int(round(self.hop_a / self.alpha)))
        self._setup_buffers()

    def _setup_buffers(self):
        self._win = np.hanning(self.win_size).astype(np.float64)
        # Input queue as a numpy deque implemented with a list
        self._in_buf = np.zeros(0, np.float64)
        # OLA output buffer: needs to be large enough to hold several frames ahead
        ola_size = self.win_size + self.hop_out * 64
        self._ola_buf = np.zeros(ola_size, np.float64)
        self._ola_norm = np.zeros(ola_size, np.float64)
        # Absolute positions for OLA write head and read head
        self._ola_write_pos = 0  # next output synthesis frame goes here (circular)
        self._out_read_pos = 0   # next sample to emit (circular)
        self._out_produced = 0   # total samples produced (absolute)
        self._out_consumed = 0   # total samples emitted (absolute)
        # Frame counter (how many input hops processed)
        self._frame_idx = 0

    def reset(self):
        self._setup_buffers()

    def _process_frames(self):
        """Process all available frames from the input buffer."""
        ola_size = len(self._ola_buf)
        while len(self._in_buf) >= self.win_size:
            frame = self._in_buf[:self.win_size] * self._win
            self._in_buf = self._in_buf[self.hop_a:]

            # Resample to target_len (pitch shift)
            resampled = scipy_resample(frame, self.target_len)

            # Overlap-add into output buffer at current write position
            base = self._ola_write_pos % ola_size
            end = base + self.target_len
            if end <= ola_size:
                self._ola_buf[base:end] += resampled
                self._ola_norm[base:end] += 1.0
            else:
                split = ola_size - base
                self._ola_buf[base:] += resampled[:split]
                self._ola_buf[:end - ola_size] += resampled[split:]
                self._ola_norm[base:] += 1.0
                self._ola_norm[:end - ola_size] += 1.0

            self._ola_write_pos += self.hop_out
            self._out_produced += self.hop_out
            self._frame_idx += 1

    def process(self, block: np.ndarray) -> np.ndarray:
        n = len(block)
        # Append input block to input buffer
        self._in_buf = np.concatenate([self._in_buf, block.astype(np.float64)])
        self._process_frames()

        # Emit n samples from OLA output buffer
        out = np.zeros(n, np.float32)
        ola_size = len(self._ola_buf)
        available = self._out_produced - self._out_consumed
        emit = min(n, available)
        for i in range(emit):
            pos = (self._out_read_pos + i) % ola_size
            norm = self._ola_norm[pos]
            val = self._ola_buf[pos] / norm if norm > 1e-8 else 0.0
            out[i] = float(np.clip(val, -1.0, 1.0))
            # Clear consumed sample
            self._ola_buf[pos] = 0.0
            self._ola_norm[pos] = 0.0
        self._out_read_pos = (self._out_read_pos + emit) % ola_size
        self._out_consumed += emit
        # Remaining out samples stay zero (warm-up)
        return out


class FormantShift(Effect):
    """Warp the spectral envelope per block via FFT magnitude interpolation.

    Per-block algorithm:
    - rfft the block
    - Compute magnitude and phase
    - Warp magnitude bins: bin k gets its energy from bin k/factor (interpolated)
      so factor>1 stretches spectrum upward (brighter/smaller), factor<1 compresses
    - Recombine warped magnitude with original phase
    - irfft back to time domain, return float32
    """

    def __init__(self, factor: float = 1.0):
        self.factor = float(factor)

    def reset(self):
        pass  # stateless per-block effect

    def process(self, block: np.ndarray) -> np.ndarray:
        n = len(block)
        spec = np.fft.rfft(block.astype(np.float64))
        mag = np.abs(spec)
        phase = np.angle(spec)

        n_bins = len(mag)
        src_bins = np.arange(n_bins, dtype=np.float64) / self.factor
        # Clamp to valid range for interpolation
        src_bins = np.clip(src_bins, 0.0, n_bins - 1)
        warped_mag = np.interp(src_bins, np.arange(n_bins), mag)

        warped_spec = warped_mag * np.exp(1j * phase)
        out = np.fft.irfft(warped_spec, n=n)
        return out.astype(np.float32)
