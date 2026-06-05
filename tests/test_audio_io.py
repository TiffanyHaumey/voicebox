def test_module_imports_and_engines_construct():
    from voicebox.engine import audio_io
    from voicebox.engine.params import Params
    p = Params()
    eng = audio_io.make_engine(p, {"Robot": []})
    assert eng is not None
    assert hasattr(eng, "start") and hasattr(eng, "stop")

def test_enumeration_functions_callable():
    from voicebox.engine import audio_io
    # may return empty lists in a headless env; just must not raise
    assert isinstance(audio_io.list_output_devices(), list)
    assert isinstance(audio_io.list_input_devices(), list)
    audio_io.find_virtual_device()  # returns None or a tuple; must not raise

def test_callback_processes_a_block_without_hardware():
    # Drive the duplex callback directly with fake buffers (no real stream).
    import numpy as np
    from voicebox.engine.audio_io import AudioEngine
    from voicebox.engine.params import Params
    p = Params()
    eng = AudioEngine(p, {"Gainy": [{"effect": "gain", "gain_db": 0}]})
    p.request_preset("Gainy")
    indata = (0.1 * np.ones((512, 1))).astype(np.float32)
    outdata = np.zeros((512, 1), dtype=np.float32)
    eng._callback(indata, outdata, 512, None, None)
    assert outdata.shape == (512, 1)
    assert np.max(np.abs(outdata)) > 0.0  # signal passed through
