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
