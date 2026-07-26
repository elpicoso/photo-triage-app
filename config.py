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

# Hamming-distance cutoff (out of 64 bits) for grouping two previews as
# near-duplicates in dedupe.group_duplicates() - lower means stricter
# (fewer false groupings), higher means looser (catches more, but risks
# grouping genuinely different shots).
DEFAULT_DUPLICATE_THRESHOLD = int(os.environ.get("DUPLICATE_THRESHOLD", "5"))

# The framing/genre sentence in the triage prompt (see triage.build_prompt())
# - e.g. swap to "a wedding" or "a corporate headshot session" for shoots
# that aren't landscape/astro.
DEFAULT_SHOOT_CONTEXT = "a landscape/astrophotography shoot"

# Evaluation criteria sent to Claude as part of the triage prompt (see
# triage.build_prompt()). These are the exact instructions the model
# follows for each field - editable via the Settings page so they can be
# tuned (e.g. reject tolerance, keyword style) without a code change.
DEFAULT_REJECT_CRITERIA = (
    '"yes" if technically bad (blurry, out of focus, blown highlights, '
    "poorly composed). Note: intentional long exposures, star trails, and "
    "motion blur in moving elements (water, clouds) are NOT rejects."
)
DEFAULT_RATING_CRITERIA = "1-5 stars if kept (use 0 if rejected)"
DEFAULT_KEYWORD_CRITERIA = (
    "5-8 specific, descriptive keywords, comma-separated "
    '(e.g. "Milky Way, granite boulders, long exposure" not "nature, sky")'
)

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
        "shoot_context": overrides.get("shoot_context", DEFAULT_SHOOT_CONTEXT),
        "duplicate_threshold": overrides.get(
            "duplicate_threshold", DEFAULT_DUPLICATE_THRESHOLD
        ),
        "reject_criteria": overrides.get("reject_criteria", DEFAULT_REJECT_CRITERIA),
        "rating_criteria": overrides.get("rating_criteria", DEFAULT_RATING_CRITERIA),
        "keyword_criteria": overrides.get("keyword_criteria", DEFAULT_KEYWORD_CRITERIA),
    }


def save_settings(settings):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
