import sys
import collections
import numpy as np
import sounddevice as sd
from voicebox.constants import SAMPLE_RATE, BLOCK_SIZE
from voicebox.engine.chain import build_chain


def list_output_devices():
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_output_channels"] > 0]


def list_input_devices():
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0]


def find_virtual_device():
    """Best-effort: match BlackHole / VB-CABLE / null sink by name."""
    needles = ("blackhole", "cable", "null", "voicebox")
    for i, name in list_output_devices():
        if any(n in name.lower() for n in needles):
            return i, name
    return None


def _fit(block, frames):
    """Pad/truncate a 1-D block to exactly `frames` samples (RT-safe, no raise)."""
    if len(block) == frames:
        return block
    fitted = np.zeros(frames, np.float32)
    n = min(len(block), frames)
    fitted[:n] = block[:n]
    return fitted


class _MonitorMixin:
    """Adds an optional headphone-monitoring output stream to an engine.

    The engine pushes each processed block into a small ring buffer via
    ``_push_monitor`` from its audio callback; a separate OutputStream drains it.
    Monitoring goes to a distinct device (the headphones / system default),
    NOT the virtual device. Small clock drift between streams is acceptable for
    self-listening. Opening/closing the stream happens on the GUI thread.
    """
    def _init_monitor(self):
        self._monitor_buf = collections.deque(maxlen=8)
        self._monitor_stream = None

    def _push_monitor(self, block):
        # block is a fresh array (np.clip output) not mutated afterward -> no copy
        if self.params.monitoring:
            self._monitor_buf.append(block)

    def _monitor_cb(self, outdata, frames, time, status):
        block = self._monitor_buf.popleft() if self._monitor_buf else np.zeros(frames, np.float32)
        outdata[:] = _fit(block, frames).reshape(-1, 1)

    def set_monitoring(self, enabled, device=None, out_channels=1):
        """Toggle self-monitoring. device=None uses the system default output."""
        self.params.monitoring = bool(enabled)
        if enabled and self._monitor_stream is None:
            self._monitor_stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                device=device, channels=out_channels, dtype="float32",
                callback=self._monitor_cb,
            )
            self._monitor_stream.start()
        elif not enabled and self._monitor_stream is not None:
            self._monitor_stream.stop(); self._monitor_stream.close()
            self._monitor_stream = None
            self._monitor_buf.clear()

    def _stop_monitor(self):
        if self._monitor_stream is not None:
            self._monitor_stream.stop(); self._monitor_stream.close()
            self._monitor_stream = None


class AudioEngine(_MonitorMixin):
    """Duplex engine: one sd.Stream for input+output. Use on macOS with an
    Aggregate Device (real mic in, BlackHole + headphones out)."""
    def __init__(self, params, preset_map):
        self.params = params
        # Pre-build every chain once, on the main thread. Preset swap in the
        # audio callback is then a plain dict lookup + reference assignment —
        # no build_chain / scipy.butter / allocation on the real-time thread.
        self._chains = {name: build_chain(spec) for name, spec in preset_map.items()}
        self.chain = build_chain([])
        self._stream = None
        self._last_block = None
        self._init_monitor()

    def _swap_preset_if_requested(self):
        name = self.params.poll_preset()
        if name and name in self._chains:
            self.chain = self._chains[name]

    def _callback(self, indata, outdata, frames, time, status):
        self._swap_preset_if_requested()
        mono = indata[:, 0] if indata.ndim > 1 else indata
        try:
            processed = self.chain(np.ascontiguousarray(mono))
        except Exception:
            processed = mono  # bypass on error (spec: dry, not silence)
        g = 10 ** (self.params.master_gain_db / 20)
        processed = np.clip(processed * g, -1.0, 1.0)
        outdata[:] = _fit(processed, frames).reshape(-1, 1)
        self._push_monitor(processed)

    def start(self, input_device, output_device, out_channels=1):
        self._stream = sd.Stream(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
            device=(input_device, output_device),
            channels=(1, out_channels), dtype="float32", callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop(); self._stream.close(); self._stream = None
        self._stop_monitor()


class DualStreamEngine(_MonitorMixin):
    """Fallback: separate input + output streams bridged by a ring buffer.
    Used when input and output devices cannot share one duplex stream
    (Windows VB-CABLE, Linux null-sink)."""
    def __init__(self, params, preset_map):
        self.params = params
        self._chains = {name: build_chain(spec) for name, spec in preset_map.items()}
        self.chain = build_chain([])
        self._buf = collections.deque(maxlen=32)
        self._in = None
        self._out = None
        self._init_monitor()

    def _in_cb(self, indata, frames, time, status):
        name = self.params.poll_preset()
        if name and name in self._chains:
            self.chain = self._chains[name]
        mono = indata[:, 0] if indata.ndim > 1 else indata
        try:
            processed = self.chain(np.ascontiguousarray(mono))
        except Exception:
            processed = np.ascontiguousarray(mono)
        g = 10 ** (self.params.master_gain_db / 20)
        processed = np.clip(processed * g, -1.0, 1.0)
        self._buf.append(processed)
        self._push_monitor(processed)

    def _out_cb(self, outdata, frames, time, status):
        block = self._buf.popleft() if self._buf else np.zeros(frames, np.float32)
        outdata[:] = _fit(block, frames).reshape(-1, 1)

    def start(self, input_device, output_device, out_channels=1):
        self._in = sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                                  device=input_device, channels=1, dtype="float32",
                                  callback=self._in_cb)
        self._out = sd.OutputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                                    device=output_device, channels=out_channels,
                                    dtype="float32", callback=self._out_cb)
        self._in.start(); self._out.start()

    def stop(self):
        for s in (self._in, self._out):
            if s:
                s.stop(); s.close()
        self._in = self._out = None
        self._stop_monitor()


def make_engine(params, preset_map):
    """Duplex AudioEngine on macOS (with Aggregate Device); DualStreamEngine elsewhere."""
    if sys.platform == "darwin":
        return AudioEngine(params, preset_map)
    return DualStreamEngine(params, preset_map)
