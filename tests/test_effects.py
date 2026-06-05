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

def test_lowpass_attenuates_high_freq():
    from voicebox.engine.effects import Filter
    high = _sine(freq=8000, n=4096)
    lp = Filter(kind="lowpass", cutoff=1000)
    out = np.concatenate([lp(high[i:i+512]) for i in range(0, 4096, 512)])
    assert np.sqrt(np.mean(out[1024:]**2)) < np.sqrt(np.mean(high[1024:]**2)) * 0.4

def test_bandpass_passes_center():
    from voicebox.engine.effects import Filter
    mid = _sine(freq=1500, n=4096)
    bp = Filter(kind="bandpass", cutoff=1500, bandwidth=1000)
    out = np.concatenate([bp(mid[i:i+512]) for i in range(0, 4096, 512)])
    assert np.sqrt(np.mean(out[2048:]**2)) > 0.1
