# Voicebox Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers-extended-cc:subagent-driven-development (if subagents available) or superpowers-extended-cc:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real-time voice changer that pipes the real microphone through a DSP effects chain into a virtual microphone device, usable as a mic input in Google Meet, Slack, and Discord.

**Architecture:** A real-time audio callback (sounddevice) runs the effects chain in an audio-priority thread, fully isolated from the PyQt GUI. The GUI only writes to a lock-free shared params object. Voices are JSON presets (ordered lists of effects). Output goes to a configurable virtual device (BlackHole/VB-CABLE/null-sink); optional monitoring feeds the same processed block to the user's headphones.

**Tech Stack:** Python 3.11+, numpy, scipy, sounddevice (PortAudio), PyQt6, jsonschema, pytest.

**Reference spec:** `docs/specs/2026-06-05-voice-changer-design.md`

**Global audio constants (used everywhere):** sample rate `48000` Hz, block size `512` samples, mono internal processing, float32 in range [-1, 1].

---

## File Structure

```
voicebox/
├── voicebox/
│   ├── __init__.py
│   ├── constants.py          # SAMPLE_RATE, BLOCK_SIZE, CHANNELS
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── effects.py        # pure DSP functions: bloc in → bloc out
│   │   ├── chain.py          # build + apply an effect chain from a preset
│   │   ├── presets.py        # load + JSON-schema-validate presets
│   │   ├── params.py         # lock-free shared state (preset swap + slider floats)
│   │   └── audio_io.py       # device enumeration, stream(s), ring buffer, monitoring
│   └── gui/
│       ├── __init__.py
│       └── app.py            # PyQt window: preset buttons, sliders, device picker, monitor toggle
├── presets/                  # 11 *.json voice presets (packaged resource)
├── tests/
│   ├── test_effects.py
│   ├── test_chain.py
│   ├── test_presets.py
│   └── test_params.py
├── main.py                   # entry point: wires engine + gui
├── requirements.txt
└── pyproject.toml            # packaging metadata (PyInstaller-friendly)
```

**Effect stateful note:** reverb, delay, and pitch_shift carry state between blocks. Effects are therefore implemented as **callable objects** (classes with `__call__(block) -> block` and a `reset()`), not bare functions. Stateless effects (gain, eq one-shot, ring_mod, bitcrush, distortion) are also classes for a uniform interface. This keeps `chain.py` simple: it holds a list of instantiated effect objects.

---

## Task 0: Project scaffold & tooling

**Files:**
- Create: `requirements.txt`, `pyproject.toml`, `voicebox/__init__.py`, `voicebox/constants.py`, `voicebox/engine/__init__.py`, `voicebox/gui/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
numpy>=1.26
scipy>=1.11
sounddevice>=0.4.6
PyQt6>=6.6
jsonschema>=4.20
pytest>=8.0
```

- [ ] **Step 2: Create `voicebox/constants.py`**

```python
SAMPLE_RATE = 48000
BLOCK_SIZE = 512
CHANNELS = 1
DTYPE = "float32"
```

- [ ] **Step 3: Create empty package files**

Create `voicebox/__init__.py`, `voicebox/engine/__init__.py`, `voicebox/gui/__init__.py`, `tests/__init__.py` (all empty).

- [ ] **Step 4: Create `pyproject.toml`** (minimal, build-friendly)

```toml
[project]
name = "voicebox"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 5: Set up venv and install**

Run: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
Expected: all packages install without error.

- [ ] **Step 6: Verify pytest runs (no tests yet)**

Run: `pytest`
Expected: "no tests ran" (exit 5) — confirms pytest is wired.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pyproject.toml voicebox tests
git commit -m "chore: project scaffold, constants, tooling"
```

---

## Task 1: Stateless effects (gain, bitcrush, distortion, ring_mod)

**Files:**
- Create: `voicebox/engine/effects.py`
- Test: `tests/test_effects.py`

These are the easiest, fully deterministic effects. Establish the effect-object interface here.

- [ ] **Step 1: Write failing tests**

```python
import numpy as np
from voicebox.engine import effects

def _sine(freq=220.0, n=512, sr=48000):
    t = np.arange(n) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)

def test_gain_scales_amplitude():
    g = effects.Gain(gain_db=6.0)
    out = g(_sine())
    assert np.max(np.abs(out)) > np.max(np.abs(_sine())) * 1.9

def test_ring_mod_changes_signal():
    rm = effects.RingMod(freq=200.0)
    inp = _sine()
    out = rm(inp)
    assert out.shape == inp.shape
    assert not np.allclose(out, inp)

def test_bitcrush_quantizes():
    bc = effects.Bitcrush(bits=4)
    out = bc(_sine())
    # far fewer unique values than the input
    assert len(np.unique(out)) <= 32

def test_distortion_clips():
    d = effects.Distortion(drive=10.0)
    out = d(_sine())
    assert np.max(np.abs(out)) <= 1.0 + 1e-6

def test_effects_preserve_shape_and_dtype():
    for fx in [effects.Gain(0), effects.RingMod(100), effects.Bitcrush(8), effects.Distortion(2)]:
        out = fx(_sine())
        assert out.shape == (512,)
        assert out.dtype == np.float32
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_effects.py -v`
Expected: FAIL (module/classes not defined).

- [ ] **Step 3: Implement the four effects + base interface**

```python
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_effects.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/effects.py tests/test_effects.py
git commit -m "feat(effects): gain, ring mod, bitcrush, distortion + Effect base"
```

---

## Task 2: EQ / filter effect

**Files:**
- Modify: `voicebox/engine/effects.py`
- Test: `tests/test_effects.py`

scipy `sosfilt` with persistent `zi` state across blocks (no click at boundaries).

- [ ] **Step 1: Write failing tests**

```python
def test_lowpass_attenuates_high_freq():
    from voicebox.engine.effects import Filter
    sr = 48000
    high = _sine(freq=8000, n=4096)
    lp = Filter(kind="lowpass", cutoff=1000)
    out = np.concatenate([lp(high[i:i+512]) for i in range(0, 4096, 512)])
    # high freq energy strongly reduced after settling
    assert np.sqrt(np.mean(out[1024:]**2)) < np.sqrt(np.mean(high[1024:]**2)) * 0.4

def test_bandpass_passes_center():
    from voicebox.engine.effects import Filter
    mid = _sine(freq=1500, n=4096)
    bp = Filter(kind="bandpass", cutoff=1500, bandwidth=1000)
    out = np.concatenate([bp(mid[i:i+512]) for i in range(0, 4096, 512)])
    assert np.sqrt(np.mean(out[2048:]**2)) > 0.1
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_effects.py -k filter_or_pass -v` (or run the two new tests)
Expected: FAIL (Filter not defined).

- [ ] **Step 3: Implement `Filter`**

```python
from scipy import signal

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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_effects.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/effects.py tests/test_effects.py
git commit -m "feat(effects): stateful butterworth filter (low/high/bandpass)"
```

---

## Task 3: Delay and Reverb effects

**Files:**
- Modify: `voicebox/engine/effects.py`
- Test: `tests/test_effects.py`

Delay = circular buffer with feedback. Reverb = small Schroeder (a few combs + allpass), lightweight per spec.

- [ ] **Step 1: Write failing tests**

```python
def test_delay_produces_echo_tail():
    from voicebox.engine.effects import Delay
    d = Delay(delay_ms=10, feedback=0.5, wet=0.8)
    impulse = np.zeros(512, np.float32); impulse[0] = 1.0
    first = d(impulse)
    later = d(np.zeros(512, np.float32))  # echo should appear in a later block
    assert np.max(np.abs(later)) > 0.05

def test_reverb_adds_tail_and_keeps_range():
    from voicebox.engine.effects import Reverb
    r = Reverb(room=0.7, wet=0.4)
    impulse = np.zeros(512, np.float32); impulse[0] = 1.0
    _ = r(impulse)
    tail = r(np.zeros(512, np.float32))
    assert np.max(np.abs(tail)) > 0.0
    assert np.max(np.abs(tail)) <= 1.0 + 1e-3
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_effects.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `Delay` and `Reverb`**

```python
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
```

> **Perf note:** the per-sample Python loop in `Delay` may be too slow at 48 kHz for several stacked instances. If profiling (Task 9 manual test) shows underruns, vectorize `Delay` using block-wise numpy indexing. Keep the simple version until proven slow (YAGNI).

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_effects.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/effects.py tests/test_effects.py
git commit -m "feat(effects): delay + lightweight Schroeder reverb"
```

---

## Task 4: Pitch shift and formant shift

**Files:**
- Modify: `voicebox/engine/effects.py`
- Test: `tests/test_effects.py`

Per spec: lightweight pitch shifter (NOT pyrubberband). Use an overlap-add / short phase-vocoder approach with internal buffering. Formant shift = spectral envelope warp via resampling of the magnitude spectrum (kept simple).

- [ ] **Step 1: Write failing test (pitch up shifts dominant frequency)**

```python
def test_pitch_shift_up_raises_pitch():
    from voicebox.engine.effects import PitchShift
    sr = 48000
    ps = PitchShift(semitones=12)  # one octave up
    sig = _sine(freq=220, n=512*60)
    out = np.concatenate([ps(sig[i:i+512]) for i in range(0, len(sig), 512)])
    out = out[len(out)//2:]  # drop the first half as warm-up (robust to vocoder latency)
    # dominant frequency via FFT should be near 440 Hz, not 220
    spec = np.abs(np.fft.rfft(out * np.hanning(len(out))))
    freqs = np.fft.rfftfreq(len(out), 1/sr)
    dom = freqs[np.argmax(spec)]
    assert 380 < dom < 500

def test_pitch_shift_preserves_block_size():
    from voicebox.engine.effects import PitchShift
    ps = PitchShift(semitones=-5)
    out = ps(_sine())
    assert out.shape == (512,)
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_effects.py -k pitch -v`
Expected: FAIL.

- [ ] **Step 3: Implement `PitchShift` (and `FormantShift`)**

Implementation guidance (the executing agent writes the concrete code):
- `PitchShift`: maintain an internal input ring buffer. Use a short-window STFT (e.g. window 1024, hop 256) phase-vocoder time-stretch by factor `2**(-semitones/12)`, then resample back to original length so output rate matches input. Keep latency < ~10 ms. Always return exactly `len(block)` samples by buffering; emit zeros during warm-up until enough input is accumulated.
- `FormantShift(factor)`: per-block FFT, warp the magnitude spectrum along frequency by `factor` (interpolate), keep original phase, inverse FFT with overlap-add. `factor>1` = brighter/"smaller", `<1` = darker/"bigger".
- Both must expose `reset()` clearing internal buffers.

> Acceptance is behavioral (the FFT test above), not implementation-specific. If the phase-vocoder proves too heavy in the Task 9 perf test, fall back to a simpler resample-and-overlap (PSOLA-style) pitch shifter — the test must still pass.

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_effects.py -v`
Expected: PASS (allow a slightly wide frequency tolerance).

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/effects.py tests/test_effects.py
git commit -m "feat(effects): lightweight pitch shift + formant shift"
```

---

## Task 5: Effect chain + registry

**Files:**
- Create: `voicebox/engine/chain.py`
- Test: `tests/test_chain.py`

Maps preset effect dicts to instantiated effect objects and applies them in order.

- [ ] **Step 1: Write failing tests**

```python
import numpy as np
from voicebox.engine.chain import build_chain, EFFECT_REGISTRY, Chain

def test_registry_has_all_effects():
    for name in ["gain","ring_mod","bitcrush","distortion","filter","delay","reverb","pitch_shift","formant_shift"]:
        assert name in EFFECT_REGISTRY

def test_build_chain_instantiates_effects():
    spec = [{"effect": "gain", "gain_db": 0}, {"effect": "pitch_shift", "semitones": 5}]
    chain = build_chain(spec)
    assert isinstance(chain, Chain)
    assert len(chain.effects) == 2

def test_chain_applies_in_order_and_preserves_shape():
    spec = [{"effect": "gain", "gain_db": 6}]
    chain = build_chain(spec)
    out = chain(np.ones(512, np.float32) * 0.1)
    assert out.shape == (512,)
    assert np.max(np.abs(out)) > 0.19

def test_empty_chain_is_passthrough():
    chain = build_chain([])
    block = np.random.randn(512).astype(np.float32) * 0.1
    assert np.allclose(chain(block), block)

def test_unknown_effect_raises():
    import pytest
    with pytest.raises(KeyError):
        build_chain([{"effect": "does_not_exist"}])
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_chain.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `chain.py`**

```python
import numpy as np
from voicebox.engine import effects

EFFECT_REGISTRY = {
    "gain": effects.Gain,
    "ring_mod": effects.RingMod,
    "bitcrush": effects.Bitcrush,
    "distortion": effects.Distortion,
    "filter": effects.Filter,
    "eq": effects.Filter,        # alias: spec uses "eq" in some presets; same impl
    "delay": effects.Delay,
    "reverb": effects.Reverb,
    "pitch_shift": effects.PitchShift,
    "formant_shift": effects.FormantShift,
}

class Chain:
    def __init__(self, effect_objs):
        self.effects = effect_objs
    def __call__(self, block: np.ndarray) -> np.ndarray:
        for fx in self.effects:
            block = fx(block)
        return block.astype(np.float32, copy=False)
    def reset(self):
        for fx in self.effects:
            fx.reset()

def build_chain(spec_list):
    objs = []
    for entry in spec_list:
        params = dict(entry)
        name = params.pop("effect")
        cls = EFFECT_REGISTRY[name]  # KeyError if unknown
        objs.append(cls(**params))
    return Chain(objs)
```

> Note: `"eq"` is an alias for `Filter`. The spec's Narrateur example uses
> `{"effect": "eq", "type": "lowshelf", ...}`, but `Filter` only supports
> `kind` ∈ {lowpass, highpass, bandpass}. Preset JSON files in Task 8 must use
> `filter` with a supported `kind` (e.g. lowpass) — do NOT pass `type`/`lowshelf`
> params that `Filter.__init__` doesn't accept, or `build_chain` will raise TypeError.

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_chain.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/chain.py tests/test_chain.py
git commit -m "feat(chain): effect registry + ordered chain application"
```

---

## Task 6: Preset loading & JSON-schema validation

**Files:**
- Create: `voicebox/engine/presets.py`
- Test: `tests/test_presets.py`

Loads presets from the `presets/` dir using a path resolved relative to the package (PyInstaller-friendly). Validates against a schema at load time (never in the audio callback).

- [ ] **Step 1: Write failing tests**

```python
import json, pytest
from voicebox.engine import presets

def test_validate_accepts_good_preset():
    good = {"name": "Test", "chain": [{"effect": "gain", "gain_db": 3}]}
    presets.validate_preset(good)  # no raise

def test_validate_rejects_missing_name():
    with pytest.raises(presets.PresetError):
        presets.validate_preset({"chain": []})

def test_validate_rejects_bad_chain_entry():
    with pytest.raises(presets.PresetError):
        presets.validate_preset({"name": "X", "chain": [{"no_effect_key": 1}]})

def test_load_preset_from_file(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"name": "V", "chain": [{"effect": "gain", "gain_db": 0}]}))
    preset = presets.load_preset_file(str(p))
    assert preset["name"] == "V"

def test_load_all_skips_invalid(tmp_path):
    (tmp_path / "ok.json").write_text(json.dumps({"name": "OK", "chain": []}))
    (tmp_path / "bad.json").write_text("{ not json")
    loaded = presets.load_all_presets(str(tmp_path))
    names = [p["name"] for p in loaded]
    assert "OK" in names and len(loaded) == 1
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_presets.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `presets.py`**

```python
import json, os, glob
import jsonschema

class PresetError(ValueError):
    pass

PRESET_SCHEMA = {
    "type": "object",
    "required": ["name", "chain"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "chain": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["effect"],
                "properties": {"effect": {"type": "string"}},
            },
        },
    },
}

def validate_preset(data):
    try:
        jsonschema.validate(data, PRESET_SCHEMA)
    except jsonschema.ValidationError as e:
        raise PresetError(str(e)) from e

def load_preset_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    validate_preset(data)
    return data

def load_all_presets(directory):
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            out.append(load_preset_file(path))
        except (PresetError, json.JSONDecodeError, OSError):
            continue  # skip invalid; GUI surfaces the count mismatch
    return out

def presets_dir():
    """Resolve presets/ relative to project root (works under PyInstaller)."""
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    return os.path.join(base, "presets")
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_presets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/presets.py tests/test_presets.py
git commit -m "feat(presets): JSON loading + schema validation + safe load-all"
```

---

## Task 7: Thread-safe params (lock-free)

**Files:**
- Create: `voicebox/engine/params.py`
- Test: `tests/test_params.py`

Per spec: preset swaps via `queue.SimpleQueue`; continuous sliders as plain floats (GIL-atomic). No lock in the audio path.

- [ ] **Step 1: Write failing tests**

```python
from voicebox.engine.params import Params

def test_default_state():
    p = Params()
    assert p.monitoring is False
    assert p.master_gain_db == 0.0
    assert p.poll_preset() is None

def test_request_and_poll_preset():
    p = Params()
    p.request_preset("Robot")
    assert p.poll_preset() == "Robot"
    assert p.poll_preset() is None  # consumed

def test_latest_preset_request_wins():
    p = Params()
    p.request_preset("A"); p.request_preset("B")
    # audio thread drains to the most recent
    assert p.poll_preset() == "B"
    assert p.poll_preset() is None

def test_monitoring_toggle():
    p = Params()
    p.monitoring = True
    assert p.monitoring is True
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_params.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `params.py`**

```python
import queue

class Params:
    def __init__(self):
        self._preset_q = queue.SimpleQueue()
        self.monitoring = False       # plain bool, GIL-atomic read/write
        self.master_gain_db = 0.0     # plain float
    def request_preset(self, name: str):
        self._preset_q.put(name)
    def poll_preset(self):
        """Audio thread calls this once per block. Returns most recent request or None."""
        name = None
        try:
            while True:
                name = self._preset_q.get_nowait()
        except queue.Empty:
            pass
        return name
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_params.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/params.py tests/test_params.py
git commit -m "feat(params): lock-free shared state for audio/GUI boundary"
```

---

## Task 8: The 11 voice presets

**Files:**
- Create: `presets/01-robot.json` … `presets/11-chantante.json` (11 files)
- Test: `tests/test_presets.py` (add an integration test that every shipped preset builds a chain)

- [ ] **Step 1: Add failing integration test**

```python
def test_all_shipped_presets_build_a_chain():
    from voicebox.engine import presets, chain
    loaded = presets.load_all_presets(presets.presets_dir())
    assert len(loaded) == 11
    for p in loaded:
        c = chain.build_chain(p["chain"])  # must not raise
        assert c is not None
```

- [ ] **Step 2: Run test, verify fail**

Run: `pytest tests/test_presets.py::test_all_shipped_presets_build_a_chain -v`
Expected: FAIL (0 != 11).

- [ ] **Step 3: Write the 11 preset JSON files**

Use only registered effects and valid params. Example (`presets/03-narrateur.json`):

```json
{
  "name": "Narrateur",
  "chain": [
    {"effect": "pitch_shift", "semitones": -3},
    {"effect": "filter", "kind": "lowpass", "cutoff": 6000},
    {"effect": "reverb", "room": 0.5, "wet": 0.2}
  ]
}
```

Create all 11 per the spec table (Robot, Grave/Deep, Narrateur, Aiguë, Démon, Alien, Bébé, Grotte, Radio, Femme↔Homme, Chantante). Chantante = chorus approximated as `formant_shift` + `reverb` for v1 (harmony deferred per spec; mark with a comment in README, not in JSON).

- [ ] **Step 4: Run test, verify pass**

Run: `pytest tests/test_presets.py -v`
Expected: PASS (11 presets build).

- [ ] **Step 5: Commit**

```bash
git add presets tests/test_presets.py
git commit -m "feat(presets): ship 11 voice presets"
```

---

## Task 9: Audio I/O engine (manual-tested)

**Files:**
- Create: `voicebox/engine/audio_io.py`

This layer touches hardware and cannot be meaningfully unit-tested; it is validated manually. Keep logic thin and push all DSP into the (already tested) chain.

- [ ] **Step 1: Implement device enumeration**

```python
import sounddevice as sd

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
```

- [ ] **Step 2: Implement the engine class**

```python
import numpy as np
import sounddevice as sd
from voicebox.constants import SAMPLE_RATE, BLOCK_SIZE
from voicebox.engine import presets as preset_mod
from voicebox.engine.chain import build_chain

class AudioEngine:
    def __init__(self, params, preset_map):
        self.params = params
        self.preset_map = preset_map           # name -> chain spec (list)
        self.chain = build_chain([])           # passthrough until a preset is chosen
        self._stream = None
        self._monitor_stream = None
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
        # outdata is always 2-D (frames, out_channels); broadcast mono across channels
        outdata[:] = processed.reshape(-1, 1)
        # TODO(monitoring): when self.params.monitoring is True and a separate
        # headphone stream/aggregate is in use, the same `processed` block feeds it.
        # v1 relies on a macOS Aggregate Device (no second stream). See Task 9 note.
        self._last_block = processed

    def start(self, input_device, output_device, out_channels=1):
        # channels must be ints. out_channels=1 -> outdata broadcast works.
        # If the chosen output device requires >1 channel, pass its count; the
        # (frames,1) assignment above broadcasts mono across all output channels.
        self._stream = sd.Stream(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
            device=(input_device, output_device),
            channels=(1, out_channels), dtype="float32", callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop(); self._stream.close(); self._stream = None
```

> Monitoring note: simplest correct approach for v1 is selecting a macOS **Aggregate Device** (real mic in, BlackHole+headphones out) as the single output device — then monitoring needs no second stream. The `monitoring` flag in the GUI documents/reminds this setup. A code-driven second monitor stream is deferred (YAGNI) unless manual testing shows it's needed on Windows/Linux.

- [ ] **Step 3: Implement the double-stream + ring-buffer fallback (Windows/Linux)**

Per spec §4: a single duplex `sd.Stream` requires input and output to share the same host
API/device. On macOS this is solved by the Aggregate Device (Step 2 path). On Windows
(VB-CABLE) and Linux (null-sink) the mic and virtual device are distinct, so use **two
streams bridged by a ring buffer**. Add this alternative engine path:

```python
import collections, threading

class DualStreamEngine:
    """Fallback: separate input + output streams bridged by a lock-free-ish ring buffer.
    Used when input and output devices cannot share one duplex stream."""
    def __init__(self, params, preset_map):
        self.params = params
        self.preset_map = preset_map
        self.chain = build_chain([])
        self._buf = collections.deque(maxlen=32)   # holds processed blocks
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
```

Add a selector so the app picks the right engine:

```python
import sys

def make_engine(params, preset_map):
    """Duplex AudioEngine on macOS (with Aggregate Device); DualStreamEngine elsewhere."""
    if sys.platform == "darwin":
        return AudioEngine(params, preset_map)
    return DualStreamEngine(params, preset_map)
```

> The deque bridge tolerates small rate jitter (maxlen caps latency growth; underflow yields
> a silent block rather than a crash). If sustained drift appears in manual testing,
> upgrade to a fixed-size sample ring buffer with under/overflow counters. Keep the deque
> until proven insufficient (YAGNI).

- [ ] **Step 4: Manual smoke test (loopback)**

Run a tiny script (in a Python REPL or `scripts/smoke.py`) that starts the engine with `input_device=default`, `output_device=default headphones`, selects the "Robot" preset, and confirms you hear your transformed voice. Document the result.

Expected: transformed audio audible, no crash, no sustained underruns (occasional xrun on first start is OK).

- [ ] **Step 5: Commit**

```bash
git add voicebox/engine/audio_io.py
git commit -m "feat(audio): real-time engine, dual-stream fallback, bypass-on-error"
```

---

## Task 10: PyQt GUI

**Files:**
- Create: `voicebox/gui/app.py`

GUI only reads/writes `Params` and starts/stops the engine. No DSP here.

- [ ] **Step 1: Implement the window**

Requirements:
- Grid of 11 preset buttons; clicking calls `params.request_preset(name)` and highlights the active one.
- Input-device and output-device dropdowns populated from `audio_io.list_input_devices()` / `list_output_devices()`; auto-select `find_virtual_device()` for output if found.
- A master-gain slider writing `params.master_gain_db`.
- A "M'entendre" checkbox writing `params.monitoring` (with a tooltip explaining the macOS Aggregate Device setup).
- Start/Stop button that calls `engine.start(in_dev, out_dev)` / `engine.stop()`.
- If no virtual device is found, show a banner with install links (BlackHole / VB-CABLE / Linux null-sink instructions).
- Show "loaded N/11 presets" so a skipped invalid preset is visible.

- [ ] **Step 2: Manual test**

Run: `python main.py`
Expected: window opens, devices listed, selecting presets while speaking (monitoring on) changes the voice live, no GUI-induced audio glitches.

- [ ] **Step 3: Commit**

```bash
git add voicebox/gui/app.py
git commit -m "feat(gui): PyQt control panel for presets, devices, monitoring"
```

---

## Task 11: Entry point & wiring

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement `main.py`**

```python
import sys
from PyQt6.QtWidgets import QApplication
from voicebox.engine import presets as preset_mod
from voicebox.engine.params import Params
from voicebox.engine.audio_io import make_engine
from voicebox.gui.app import MainWindow

def main():
    loaded = preset_mod.load_all_presets(preset_mod.presets_dir())
    preset_map = {p["name"]: p["chain"] for p in loaded}
    params = Params()
    engine = make_engine(params, preset_map)  # duplex on macOS, dual-stream elsewhere
    app = QApplication(sys.argv)
    win = MainWindow(params, engine, loaded)
    win.show()
    code = app.exec()
    engine.stop()
    sys.exit(code)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual end-to-end test**

Run: `python main.py`, select BlackHole/VB-CABLE as output, open Google Meet test (`meet.google.com` → check your mic), choose the virtual device as mic in Meet, confirm transformed voice is heard in Meet's mic test.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: application entry point wiring engine + gui"
```

---

## Task 12: Docs for virtual-device setup

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add per-OS setup instructions**

Document: installing BlackHole (macOS) + creating an Aggregate Device for monitoring; installing VB-CABLE (Windows); creating a PipeWire/PulseAudio null-sink (Linux); and how to pick the virtual device as mic in Meet/Slack/Discord.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: per-OS virtual microphone setup guide"
```

---

## Done criteria

- `pytest` is green (effects, chain, presets, params).
- 11 presets load and build chains.
- Manual: transformed voice audible via monitoring and selectable as mic in Meet/Slack/Discord.
- No packaging yet (deferred per spec).
