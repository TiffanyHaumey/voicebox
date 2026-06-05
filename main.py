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
