import json
import os

# Fallback defaults. These are how every setting below used to be
# configured (env vars / hardcoded), and remain the fallback for any key
# missing from settings.json (including when the file doesn't exist at all).
DEFAULT_RAW_ROOT = os.environ.get("RAW_ROOT", "/mnt/photos")
DEFAULT_TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "claude-haiku-4-5-20251001")
DEFAULT_BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))
DEFAULT_MAX_PREVIEW_DIMENSION = int(os.environ.get("MAX_PREVIEW_DIMENSION", "1568"))
DEFAULT_RAW_EXTENSIONS = ["cr2", "cr3", "nef", "arw", "raf", "dng", "orf", "rw2"]

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def load_settings():
    """Runtime-editable overrides written by the /settings page. Read fresh
    on every call (cheap - it's a tiny local file) so edits take effect
    immediately, with no service restart needed."""
    overrides = {}
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH) as f:
            overrides = json.load(f)
    return {
        "raw_root": overrides.get("raw_root", DEFAULT_RAW_ROOT),
        "triage_model": overrides.get("triage_model", DEFAULT_TRIAGE_MODEL),
        "batch_size": overrides.get("batch_size", DEFAULT_BATCH_SIZE),
        "max_preview_dimension": overrides.get(
            "max_preview_dimension", DEFAULT_MAX_PREVIEW_DIMENSION
        ),
        "raw_extensions": set(
            e.lower() for e in overrides.get("raw_extensions", DEFAULT_RAW_EXTENSIONS)
        ),
    }


def save_settings(settings):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
