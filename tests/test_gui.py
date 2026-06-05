import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

def test_mainwindow_constructs_offscreen():
    from PyQt6.QtWidgets import QApplication
    from voicebox.gui.app import MainWindow
    from voicebox.engine.params import Params
    app = QApplication.instance() or QApplication([])
    params = Params()
    class FakeEngine:
        def __init__(self): self.started = False
        def start(self, *a, **k): self.started = True
        def stop(self): self.started = False
    loaded = [{"name": "Robot", "chain": []}, {"name": "Aiguë", "chain": []}]
    win = MainWindow(params, FakeEngine(), loaded)
    assert win is not None

def test_preset_button_requests_preset():
    from PyQt6.QtWidgets import QApplication
    from voicebox.gui.app import MainWindow
    from voicebox.engine.params import Params
    app = QApplication.instance() or QApplication([])
    params = Params()
    class FakeEngine:
        def start(self, *a, **k): pass
        def stop(self): pass
    loaded = [{"name": "Robot", "chain": []}]
    win = MainWindow(params, FakeEngine(), loaded)
    # simulate selecting the first preset programmatically
    win.select_preset("Robot")
    assert params.poll_preset() == "Robot"
