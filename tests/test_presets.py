import json, pytest
from voicebox.engine import presets

def test_validate_accepts_good_preset():
    good = {"name": "Test", "chain": [{"effect": "gain", "gain_db": 3}]}
    presets.validate_preset(good)  # no raise

def test_validate_rejects_missing_name():
    with pytest.raises(presets.PresetError):
        presets.validate_preset({"chain": []})

def test_validate_rejects_bad_chain_entry():
    with pytest.raises(presets.PresetError):
        presets.validate_preset({"name": "X", "chain": [{"no_effect_key": 1}]})

def test_load_preset_from_file(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"name": "V", "chain": [{"effect": "gain", "gain_db": 0}]}))
    preset = presets.load_preset_file(str(p))
    assert preset["name"] == "V"

def test_load_all_skips_invalid(tmp_path):
    (tmp_path / "ok.json").write_text(json.dumps({"name": "OK", "chain": []}))
    (tmp_path / "bad.json").write_text("{ not json")
    loaded = presets.load_all_presets(str(tmp_path))
    names = [p["name"] for p in loaded]
    assert "OK" in names and len(loaded) == 1

def test_all_shipped_presets_build_a_chain():
    from voicebox.engine import presets, chain
    loaded = presets.load_all_presets(presets.presets_dir())
    assert len(loaded) == 11
    for p in loaded:
        c = chain.build_chain(p["chain"])  # must not raise
        assert c is not None
