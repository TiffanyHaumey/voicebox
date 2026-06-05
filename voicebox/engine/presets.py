import json, os, glob
import jsonschema

class PresetError(ValueError):
    pass

PRESET_SCHEMA = {
    "type": "object",
    "required": ["name", "chain"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "chain": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["effect"],
                "properties": {"effect": {"type": "string"}},
            },
        },
    },
}

def validate_preset(data):
    try:
        jsonschema.validate(data, PRESET_SCHEMA)
    except jsonschema.ValidationError as e:
        raise PresetError(str(e)) from e

def load_preset_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    validate_preset(data)
    return data

def load_all_presets(directory):
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            out.append(load_preset_file(path))
        except (PresetError, json.JSONDecodeError, OSError):
            continue  # skip invalid; GUI surfaces the count mismatch
    return out

def presets_dir():
    """Resolve presets/ relative to project root (works under PyInstaller)."""
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    return os.path.join(base, "presets")
