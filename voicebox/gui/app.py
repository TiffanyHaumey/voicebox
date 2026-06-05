"""PyQt6 control panel for the Voicebox real-time voice changer.

Layout (top to bottom):
  - Status bar: "Presets chargés : N/11"
  - Virtual-device banner (only when no virtual device is found)
  - Preset buttons grid (one per loaded preset, checkable/highlighted)
  - Device selectors: Input device + Output device dropdowns
  - Master gain slider (-12 .. +12 dB)
  - "M'entendre" monitoring checkbox
  - Start/Stop toggle button
"""

import sys
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QPushButton, QComboBox, QSlider, QCheckBox,
    QLabel, QGroupBox, QButtonGroup, QSizePolicy,
)
from PyQt6.QtCore import Qt

from voicebox.engine.audio_io import (
    list_input_devices,
    list_output_devices,
    find_virtual_device,
)


_MONITORING_TOOLTIP = (
    "M'entendre — entend votre voix transformée dans votre casque.\n\n"
    "macOS : créez un Périphérique agrégé dans Configuration Audio MIDI :\n"
    "  • Entrée  : votre micro réel\n"
    "  • Sorties : BlackHole 2ch  +  votre casque/haut-parleur\n"
    "Sélectionnez ce périphérique agrégé comme sortie dans l'application."
)

_VIRTUAL_DEVICE_HINTS = {
    "darwin": (
        "Aucun périphérique virtuel détecté.\n"
        "macOS → installez BlackHole (https://existential.audio/blackhole/) "
        "puis créez un Périphérique agrégé dans Configuration Audio MIDI."
    ),
    "win32": (
        "Aucun périphérique virtuel détecté.\n"
        "Windows → installez VB-CABLE (https://vb-audio.com/Cable/)."
    ),
    "linux": (
        "Aucun périphérique virtuel détecté.\n"
        "Linux → activez un null-sink PulseAudio/PipeWire :\n"
        "  pactl load-module module-null-sink sink_name=voicebox"
    ),
}


def _virtual_hint_text():
    key = sys.platform if sys.platform in _VIRTUAL_DEVICE_HINTS else "linux"
    return _VIRTUAL_DEVICE_HINTS[key]


class MainWindow(QMainWindow):
    """Main control panel window.

    Args:
        params: a ``Params`` instance (thread-safe parameter bridge).
        engine: an ``AudioEngine`` or ``DualStreamEngine`` instance.
        loaded: list of preset dicts, each ``{"name": str, "chain": [...]}``.
    """

    def __init__(self, params, engine, loaded):
        super().__init__()
        self._params = params
        self._engine = engine
        self._loaded = loaded
        self._running = False
        self._active_preset = None

        self.setWindowTitle("Voicebox")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        # --- Status label ---
        self._status_label = QLabel(f"Presets chargés : {len(loaded)}/11")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_label)

        # --- Virtual-device banner (only shown when no virtual device) ---
        self._virtual_device = find_virtual_device()
        if self._virtual_device is None:
            banner = QLabel(_virtual_hint_text())
            banner.setWordWrap(True)
            banner.setObjectName("virtualBanner")
            banner.setStyleSheet(
                "QLabel#virtualBanner {"
                "  background: #fff3cd; color: #856404;"
                "  border: 1px solid #ffc107; border-radius: 4px;"
                "  padding: 6px;"
                "}"
            )
            root.addWidget(banner)

        # --- Preset buttons grid ---
        preset_group = QGroupBox("Presets")
        preset_layout = QGridLayout(preset_group)
        preset_layout.setSpacing(4)
        self._preset_buttons: dict[str, QPushButton] = {}
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        cols = 3
        for idx, preset in enumerate(loaded):
            name = preset["name"]
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            # Connect using a default-argument capture to avoid late-binding
            btn.clicked.connect(lambda checked, n=name: self.select_preset(n))
            self._btn_group.addButton(btn)
            self._preset_buttons[name] = btn
            preset_layout.addWidget(btn, idx // cols, idx % cols)

        root.addWidget(preset_group)

        # --- Device selectors ---
        device_group = QGroupBox("Périphériques audio")
        device_layout = QVBoxLayout(device_group)

        in_row = QHBoxLayout()
        in_row.addWidget(QLabel("Entrée :"))
        self._input_combo = QComboBox()
        for dev_idx, dev_name in list_input_devices():
            self._input_combo.addItem(dev_name, userData=dev_idx)
        in_row.addWidget(self._input_combo)
        device_layout.addLayout(in_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Sortie :"))
        self._output_combo = QComboBox()
        for dev_idx, dev_name in list_output_devices():
            self._output_combo.addItem(dev_name, userData=dev_idx)
        # Auto-select virtual device in output if present
        if self._virtual_device is not None:
            virt_idx, _virt_name = self._virtual_device
            for combo_pos in range(self._output_combo.count()):
                if self._output_combo.itemData(combo_pos) == virt_idx:
                    self._output_combo.setCurrentIndex(combo_pos)
                    break
        out_row.addWidget(self._output_combo)
        device_layout.addLayout(out_row)

        root.addWidget(device_group)

        # --- Master gain slider ---
        gain_group = QGroupBox("Volume maître")
        gain_layout = QHBoxLayout(gain_group)
        gain_layout.addWidget(QLabel("-12 dB"))
        self._gain_slider = QSlider(Qt.Orientation.Horizontal)
        self._gain_slider.setRange(-12, 12)
        self._gain_slider.setValue(int(params.master_gain_db))
        self._gain_slider.setTickInterval(3)
        self._gain_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._gain_label = QLabel(f"{int(params.master_gain_db):+d} dB")
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        gain_layout.addWidget(self._gain_slider)
        gain_layout.addWidget(QLabel("+12 dB"))
        gain_layout.addWidget(self._gain_label)
        root.addWidget(gain_group)

        # --- Monitoring checkbox ---
        self._monitoring_cb = QCheckBox("M'entendre")
        self._monitoring_cb.setChecked(params.monitoring)
        self._monitoring_cb.setToolTip(_MONITORING_TOOLTIP)
        self._monitoring_cb.toggled.connect(self._on_monitoring_toggled)
        root.addWidget(self._monitoring_cb)

        # --- Start / Stop button ---
        self._start_stop_btn = QPushButton("▶  Démarrer")
        self._start_stop_btn.setCheckable(True)
        self._start_stop_btn.setObjectName("startStopBtn")
        self._start_stop_btn.setStyleSheet(
            "QPushButton#startStopBtn { font-weight: bold; padding: 6px; }"
            "QPushButton#startStopBtn:checked { background: #d4edda; color: #155724; }"
        )
        self._start_stop_btn.clicked.connect(self._on_start_stop)
        root.addWidget(self._start_stop_btn)

    # ------------------------------------------------------------------
    # Public API used by tests and by button callbacks
    # ------------------------------------------------------------------

    def select_preset(self, name: str):
        """Request a preset by name and highlight its button."""
        self._active_preset = name
        self._params.request_preset(name)
        btn = self._preset_buttons.get(name)
        if btn is not None:
            btn.setChecked(True)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_gain_changed(self, value: int):
        self._params.master_gain_db = float(value)
        self._gain_label.setText(f"{value:+d} dB")

    def _on_monitoring_toggled(self, checked: bool):
        # Prefer the engine's real monitor stream (routes to the default output /
        # headphones). Fall back to the plain flag if the engine lacks it.
        set_monitoring = getattr(self._engine, "set_monitoring", None)
        if callable(set_monitoring):
            set_monitoring(checked)
        else:
            self._params.monitoring = checked

    def _on_start_stop(self, checked: bool):
        if checked:
            if not self._running:
                in_idx = self._input_combo.currentData()
                out_idx = self._output_combo.currentData()
                self._engine.start(in_idx, out_idx)
                self._running = True
                self._start_stop_btn.setText("■  Arrêter")
        else:
            if self._running:
                self._engine.stop()
                self._running = False
                self._start_stop_btn.setText("▶  Démarrer")
