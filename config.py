import os

# Top of the browsable tree on the Pi (the SMB mount point). The dashboard
# lets you navigate down from here to whatever depth your shoot folders
# actually live at - no need to hardcode a specific year/subfolder.
RAW_ROOT = os.environ.get("RAW_ROOT", "/mnt/photos")

TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "claude-haiku-4-5-20251001")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))

# Claude's vision doesn't benefit from images larger than this on the long
# edge (it downsizes internally) - and camera-embedded RAW previews vary
# wildly in size (some bodies embed full-resolution previews 5-10MB+ each),
# so skipping the resize let a batch of them blow past the API's request
# size limit (413 request_too_large).
MAX_PREVIEW_DIMENSION = int(os.environ.get("MAX_PREVIEW_DIMENSION", "1568"))

# Extensions checked to decide whether a folder is a "shoot" (contains RAW
# files directly) versus just an intermediate folder to browse through.
RAW_EXTENSIONS = {"cr2", "cr3", "nef", "arw", "raf", "dng", "orf", "rw2"}
