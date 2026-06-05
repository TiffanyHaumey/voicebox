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


class AudioEngine:
    """Duplex engine: one sd.Stream for input+output. Use on macOS with an
    Aggregate Device (real mic in, BlackHole + headphones out)."""
    def __init__(self, params, preset_map):
        self.params = params
        self.preset_map = preset_map
        self.chain = build_chain([])
        self._stream = None
        self._last_block = None

    def _swap_preset_if_requested(self):
        name = self.params.poll_preset()
        if name and name in self.preset_map:
            self.chain = build_chain(self.preset_map[name])

    def _callback(self, indata, outdata, frames, time, status):
        self._swap_preset_if_requested()
        mono = indata[:, 0] if indata.ndim > 1 else indata
        try:
            processed = self.chain(np.ascontiguousarray(mono))
        except Exception:
            processed = mono  # bypass on error (spec: dry, not silence)
        g = 10 ** (self.params.master_gain_db / 20)
        processed = np.clip(processed * g, -1.0, 1.0)
        outdata[:] = processed.reshape(-1, 1)
        self._last_block = processed

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


class DualStreamEngine:
    """Fallback: separate input + output streams bridged by a ring buffer.
    Used when input and output devices cannot share one duplex stream
    (Windows VB-CABLE, Linux null-sink)."""
    def __init__(self, params, preset_map):
        self.params = params
        self.preset_map = preset_map
        self.chain = build_chain([])
        self._buf = collections.deque(maxlen=32)
        self._in = None
        self._out = None

    def _in_cb(self, indata, frames, time, status):
        name = self.params.poll_preset()
        if name and name in self.preset_map:
            self.chain = build_chain(self.preset_map[name])
        mono = indata[:, 0] if indata.ndim > 1 else indata
        try:
            processed = self.chain(np.ascontiguousarray(mono))
        except Exception:
            processed = np.ascontiguousarray(mono)
        g = 10 ** (self.params.master_gain_db / 20)
        self._buf.append(np.clip(processed * g, -1.0, 1.0))

    def _out_cb(self, outdata, frames, time, status):
        block = self._buf.popleft() if self._buf else np.zeros(frames, np.float32)
        # guard against frames != buffered block size (device start/teardown):
        # pad with zeros or truncate so the assignment never raises in the RT thread
        if len(block) != frames:
            fitted = np.zeros(frames, np.float32)
            n = min(len(block), frames)
            fitted[:n] = block[:n]
            block = fitted
        outdata[:] = block.reshape(-1, 1)

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


def make_engine(params, preset_map):
    """Duplex AudioEngine on macOS (with Aggregate Device); DualStreamEngine elsewhere."""
    if sys.platform == "darwin":
        return AudioEngine(params, preset_map)
    return DualStreamEngine(params, preset_map)
