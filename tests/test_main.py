import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

def test_main_module_imports():
    import main
    assert hasattr(main, "main")

def test_presets_load_and_map_builds():
    # the real presets dir should yield 11 presets and a name->chain map
    from voicebox.engine import presets as preset_mod
    loaded = preset_mod.load_all_presets(preset_mod.presets_dir())
    assert len(loaded) == 11
    preset_map = {p["name"]: p["chain"] for p in loaded}
    assert len(preset_map) == 11

def test_wiring_constructs_window(monkeypatch):
    # Build the same objects main() builds, but don't run the event loop.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    from voicebox.engine import presets as preset_mod
    from voicebox.engine.params import Params
    from voicebox.engine.audio_io import make_engine
    from voicebox.gui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    loaded = preset_mod.load_all_presets(preset_mod.presets_dir())
    preset_map = {p["name"]: p["chain"] for p in loaded}
    params = Params()
    engine = make_engine(params, preset_map)
    win = MainWindow(params, engine, loaded)
    assert win is not None
