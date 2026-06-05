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
