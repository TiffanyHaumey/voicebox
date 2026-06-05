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

class Limiter(Effect):
    """Smooth peak limiter with a soft knee and per-sample release.

    A hard ``np.clip`` flattens peaks into square-ish edges that buzz. This
    tracks a running gain-reduction envelope: when a sample would exceed
    ``threshold`` the target gain drops instantly (fast attack), then recovers
    gradually (``release`` per sample). The whole block is scaled by the smoothed
    gain, so transients are tamed without the gritty edge of hard clipping. A
    final tanh catches any residual overshoot."""
    def __init__(self, threshold: float = 0.9, release: float = 0.9995):
        self.threshold = float(threshold)
        self.release = float(release)
        self._gain = 1.0
    def reset(self):
        self._gain = 1.0
    def process(self, block):
        out = np.empty_like(block)
        thr = self.threshold
        g = self._gain
        rel = self.release
        for i, x in enumerate(block):
            peak = abs(x) * g
            if peak > thr:
                g = thr / (abs(x) + 1e-9)          # instant attack
            else:
                g = g * rel + (1.0 - rel)          # ease back toward unity
                if g > 1.0:
                    g = 1.0
            out[i] = x * g
        self._gain = g
        return np.tanh(out * 1.05) / np.tanh(1.05)

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
    """Streaming phase vocoder pitch shift (constant duration).

    This is the Bernsee ``smbPitchShift`` algorithm (public domain), adapted to
    block-by-block streaming. Unlike a plain resample-and-overlap-add, it tracks
    the *true* instantaneous frequency of each FFT bin from the phase difference
    between successive analysis frames, shifts those bins by the pitch ratio, and
    re-synthesises with a continuously accumulated phase. Tracking true phase is
    what removes the metallic / "phasy" artefacts the naive method produces.

    Per analysis frame (hop H, window N, 4x overlap):
      - Hann-window the frame, rFFT -> magnitude + phase.
      - dphi = phase - last_phase, minus the expected advance (2*pi*k*H/N),
        wrapped to [-pi, pi] -> the bin's true frequency deviation in bins.
      - Shift: bin k moves to round(k * ratio), accumulating magnitude and
        carrying the (scaled) true frequency to the destination bin.
      - Synthesis: advance a persistent per-bin phase by the destination true
        frequency, rebuild the spectrum, irFFT, Hann-window again, overlap-add.
    The synthesis window^2 sum is accumulated and divided out for COLA-correct
    reconstruction. Duration is preserved (analysis hop == synthesis hop).
    """

    def __init__(self, semitones: float = 0.0, win_size: int = 1024, hop_a: int = 256,
                 preserve_formants: bool = False):
        self.semitones = float(semitones)
        self.win_size = int(win_size)
        self.hop_a = int(hop_a)
        self.preserve_formants = bool(preserve_formants)
        self.ratio = 2.0 ** (self.semitones / 12.0)
        self._n_bins = self.win_size // 2 + 1
        # Expected phase advance per bin over one hop, and bins->Hz scale.
        k = np.arange(self._n_bins, dtype=np.float64)
        self._omega = 2.0 * np.pi * self.hop_a * k / self.win_size
        self._win = np.hanning(self.win_size).astype(np.float64)
        # Synthesis trim margin: latency from window length.
        self._setup_buffers()

    def _setup_buffers(self):
        self._in_buf = np.zeros(0, np.float64)
        self._last_phase = np.zeros(self._n_bins, np.float64)
        self._sum_phase = np.zeros(self._n_bins, np.float64)
        # Linear output accumulators with an absolute base index.
        self._out_acc = np.zeros(0, np.float64)
        self._out_norm = np.zeros(0, np.float64)
        self._buf_base = 0     # absolute sample index of _out_acc[0]
        self._write_pos = 0    # absolute index where the next frame is written
        self._emit_pos = 0     # absolute index of the next sample to emit

    def reset(self):
        self._setup_buffers()

    def _ensure_capacity(self, abs_end):
        need = abs_end - self._buf_base
        if need > len(self._out_acc):
            grow = need - len(self._out_acc)
            self._out_acc = np.concatenate([self._out_acc, np.zeros(grow, np.float64)])
            self._out_norm = np.concatenate([self._out_norm, np.zeros(grow, np.float64)])

    def _process_frame(self):
        N, H = self.win_size, self.hop_a
        frame = self._in_buf[:N] * self._win
        self._in_buf = self._in_buf[H:]

        spec = np.fft.rfft(frame)
        mag = np.abs(spec)
        phase = np.angle(spec)

        # True per-bin frequency (in bins) from phase difference.
        dphi = phase - self._last_phase
        self._last_phase = phase
        dphi -= self._omega
        dphi = np.mod(dphi + np.pi, 2.0 * np.pi) - np.pi
        true_freq = np.arange(self._n_bins) + dphi * self.win_size / (2.0 * np.pi * H)

        if self.preserve_formants:
            # Keep the original spectral envelope: shift only the fine structure
            # by warping the magnitude back down so formants stay put.
            mag = self._whiten(mag)

        # Shift bins by the pitch ratio.
        k = np.arange(self._n_bins)
        target = np.round(k * self.ratio).astype(np.int64)
        valid = target < self._n_bins
        syn_mag = np.zeros(self._n_bins, np.float64)
        syn_freq = np.zeros(self._n_bins, np.float64)
        np.add.at(syn_mag, target[valid], mag[valid])
        syn_freq[target[valid]] = true_freq[valid] * self.ratio

        if self.preserve_formants:
            syn_mag = self._reapply_envelope(syn_mag, np.abs(spec))

        # Accumulate synthesis phase from the shifted true frequencies.
        self._sum_phase += 2.0 * np.pi * H * syn_freq / self.win_size
        out_spec = syn_mag * np.exp(1j * self._sum_phase)
        out_frame = np.fft.irfft(out_spec, n=N) * self._win

        self._ensure_capacity(self._write_pos + N)
        s = self._write_pos - self._buf_base
        self._out_acc[s:s + N] += out_frame
        self._out_norm[s:s + N] += self._win ** 2
        self._write_pos += H

    def _whiten(self, mag):
        env = self._envelope(mag)
        return mag / env

    def _reapply_envelope(self, mag, orig_mag):
        return mag * self._envelope(orig_mag)

    def _envelope(self, mag, lifter=24):
        # Cepstral smoothing: low-quefrency liftering of log-magnitude.
        log_mag = np.log(mag + 1e-9)
        full = np.concatenate([log_mag, log_mag[-2:0:-1]])
        ceps = np.fft.rfft(full)
        ceps[lifter:] = 0.0
        smooth = np.fft.irfft(ceps, n=len(full))[:self._n_bins]
        return np.exp(smooth) + 1e-9

    def process(self, block: np.ndarray) -> np.ndarray:
        n = len(block)
        self._in_buf = np.concatenate([self._in_buf, block.astype(np.float64)])
        while len(self._in_buf) >= self.win_size:
            self._process_frame()

        out = np.zeros(n, np.float32)
        # Only emit positions already covered by the full overlap (one window
        # behind the write head), so every sample has its complete COLA sum.
        ready = self._write_pos - self.win_size
        avail = ready - self._emit_pos
        emit = max(0, min(n, avail))
        if emit > 0:
            s = self._emit_pos - self._buf_base
            seg = self._out_acc[s:s + emit]
            nrm = self._out_norm[s:s + emit]
            vals = np.divide(seg, nrm, out=np.zeros_like(seg), where=nrm > 1e-8)
            out[:emit] = np.clip(vals, -1.0, 1.0).astype(np.float32)
            self._emit_pos += emit
            # Trim finalised samples from the front to bound memory.
            trim = self._emit_pos - self._buf_base
            if trim > self.win_size * 4:
                self._out_acc = self._out_acc[trim:]
                self._out_norm = self._out_norm[trim:]
                self._buf_base = self._emit_pos
        return out


class FormantShift(Effect):
    """Shift formants while leaving pitch (harmonic structure) intact.

    Naively warping the whole magnitude spectrum also drags the harmonics, which
    changes pitch and rings. Instead this separates the signal into:
      - spectral envelope (the formants / vocal-tract shape), via cepstral
        liftering — keep only the low-quefrency part of the log-magnitude;
      - fine structure (the harmonics / source) = magnitude / envelope.
    Only the envelope is frequency-warped by ``factor`` (factor>1 = brighter /
    "smaller head", factor<1 = darker / "bigger head"), then multiplied back onto
    the untouched fine structure. Original phase is preserved.
    """

    def __init__(self, factor: float = 1.0, lifter: int = 24):
        self.factor = float(factor)
        self.lifter = int(lifter)

    def reset(self):
        pass  # stateless per-block effect

    def _envelope(self, mag, n_bins):
        log_mag = np.log(mag + 1e-9)
        full = np.concatenate([log_mag, log_mag[-2:0:-1]])
        ceps = np.fft.rfft(full)
        ceps[self.lifter:] = 0.0
        smooth = np.fft.irfft(ceps, n=len(full))[:n_bins]
        return np.exp(smooth)

    def process(self, block: np.ndarray) -> np.ndarray:
        n = len(block)
        spec = np.fft.rfft(block.astype(np.float64))
        mag = np.abs(spec)
        phase = np.angle(spec)
        n_bins = len(mag)

        env = self._envelope(mag, n_bins)
        fine = mag / (env + 1e-9)  # harmonic structure, formant-flat

        # Warp ONLY the envelope: new bin k samples the old envelope at k/factor.
        src = np.clip(np.arange(n_bins, dtype=np.float64) / self.factor, 0.0, n_bins - 1)
        warped_env = np.interp(src, np.arange(n_bins), env)

        new_mag = fine * warped_env
        out = np.fft.irfft(new_mag * np.exp(1j * phase), n=n)
        return out.astype(np.float32)
