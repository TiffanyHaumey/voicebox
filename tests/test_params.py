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
