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

def test_chains_are_prebuilt_not_built_in_callback():
    # Preset swap in the callback must reuse a pre-built chain object, never
    # call build_chain on the real-time thread.
    from voicebox.engine.audio_io import AudioEngine
    from voicebox.engine.params import Params
    p = Params()
    eng = AudioEngine(p, {"A": [{"effect": "gain", "gain_db": 0}],
                          "B": [{"effect": "gain", "gain_db": 6}]})
    assert set(eng._chains) == {"A", "B"}
    p.request_preset("B")
    eng._swap_preset_if_requested()
    assert eng.chain is eng._chains["B"]  # same object, not a fresh build

def test_callback_handles_frame_size_mismatch():
    # A callback asked for fewer frames than a block must not raise.
    import numpy as np
    from voicebox.engine.audio_io import AudioEngine
    from voicebox.engine.params import Params
    eng = AudioEngine(Params(), {"G": [{"effect": "gain", "gain_db": 0}]})
    indata = (0.1 * np.ones((256, 1))).astype(np.float32)
    outdata = np.zeros((256, 1), dtype=np.float32)
    eng._callback(indata, outdata, 256, None, None)
    assert outdata.shape == (256, 1)

def test_monitoring_api_present_and_pushes_when_enabled():
    import numpy as np
    from voicebox.engine.audio_io import AudioEngine
    from voicebox.engine.params import Params
    p = Params()
    eng = AudioEngine(p, {"G": [{"effect": "gain", "gain_db": 0}]})
    assert hasattr(eng, "set_monitoring")
    # without opening a real stream, the buffer only fills when monitoring is on
    indata = (0.2 * np.ones((512, 1))).astype(np.float32)
    outdata = np.zeros((512, 1), dtype=np.float32)
    eng._callback(indata, outdata, 512, None, None)
    assert len(eng._monitor_buf) == 0          # monitoring off by default
    p.monitoring = True
    eng._callback(indata, outdata, 512, None, None)
    assert len(eng._monitor_buf) == 1          # block queued for headphones
