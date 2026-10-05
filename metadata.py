import json
import os
import subprocess
import tempfile
import time
from collections import Counter

from PIL import Image


def _ext(filename):
    return filename.rsplit(".", 1)[1].lower()


def list_raw_files(dir_path, raw_extensions):
    """RAW filenames directly in dir_path, sorted. Skips dotfiles - SMB
    shares from a Mac expose AppleDouble stubs like "._IMG_1.CR2" that
    carry a RAW extension but aren't photos."""
    try:
        names = os.listdir(dir_path)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []
    return sorted(
        f
        for f in names
        if "." in f and not f.startswith(".") and _ext(f) in raw_extensions
    )


def raw_files_by_stem(dir_path, raw_extensions):
    """({stem: raw filename}, [filenames skipped]) for the RAW files in
    dir_path, so a folder that mixes camera bodies (.CR3 and .CR2, say) is
    handled in one pass instead of assuming a single extension.

    Previews are named <stem>.jpg and sidecars <stem>.xmp, so two RAW files
    sharing a stem across extensions would collide on both. The more common
    extension wins and the rest are returned as skipped - Lightroom itself
    can't keep two same-named RAWs in one folder apart, since they'd share
    a sidecar."""
    files = list_raw_files(dir_path, raw_extensions)
    freq = Counter(_ext(f) for f in files)
    by_stem, skipped = {}, []
    for f in sorted(files, key=lambda f: (-freq[_ext(f)], _ext(f), f)):
        stem = f.rsplit(".", 1)[0]
        if stem in by_stem:
            skipped.append(f)
        else:
            by_stem[stem] = f
    return by_stem, skipped


ORIENTATION_FILE = ".orientation.json"

# EXIF Orientation value -> the PIL transpose that makes the image upright.
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


def apply_orientation(img, orientation):
    """Rotate/flip img upright for an EXIF Orientation value (1 = already
    upright). The embedded previews carry no rotation of their own - the
    camera records it only in the RAW file - so a portrait shot's preview
    is a sideways landscape image until this is applied."""
    op = _TRANSPOSE.get(orientation)
    return img.transpose(op) if op is not None else img


def read_orientations(preview_dir):
    """{stem: EXIF orientation} saved for this folder (empty if unknown)."""
    try:
        with open(os.path.join(preview_dir, ORIENTATION_FILE)) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def ensure_orientations(raw_dir, preview_dir, raw_extensions):
    """Reads Orientation from each RAW (headers only, one exiftool call) for
    any previewed photo not yet recorded, and saves it to
    _previews/.orientation.json so triage and the review thumbnails can show
    each preview the right way up."""
    by_stem, _ = raw_files_by_stem(raw_dir, raw_extensions)
    known = read_orientations(preview_dir)
    todo = [
        os.path.join(raw_dir, f)
        for stem, f in sorted(by_stem.items())
        if stem not in known and _preview_ok(preview_dir, stem)
    ]
    if not todo:
        return known
    with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False) as argfile:
        argfile.write("\n".join(todo) + "\n")
    try:
        out = subprocess.run(
            ["exiftool", "-j", "-n", "-Orientation", "-@", argfile.name],
            capture_output=True, text=True,
        ).stdout
    finally:
        os.unlink(argfile.name)
    for row in json.loads(out) if out.strip() else []:
        stem = os.path.splitext(os.path.basename(row["SourceFile"]))[0]
        known[stem] = int(row.get("Orientation") or 1)
    path = os.path.join(preview_dir, ORIENTATION_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(known, f)
    os.replace(tmp, path)
    return known


def _preview_ok(preview_dir, stem):
    path = os.path.join(preview_dir, stem + ".jpg")
    return os.path.isfile(path) and os.path.getsize(path) > 0


def missing_previews(raw_dir, preview_dir, raw_extensions):
    """RAW filenames that don't have a (non-empty) preview yet."""
    by_stem, _ = raw_files_by_stem(raw_dir, raw_extensions)
    return [f for stem, f in sorted(by_stem.items()) if not _preview_ok(preview_dir, stem)]


def find_preview_tag(sample_raw_path):
    """Most cameras use PreviewImage; some Nikon/Sony bodies use JpgFromRaw.
    Check the actual file rather than assuming."""
    for tag in ("-PreviewImage", "-JpgFromRaw"):
        result = subprocess.run(
            ["exiftool", tag, sample_raw_path], capture_output=True, text=True
        )
        if result.stdout.strip():
            return tag
    return "-PreviewImage"


def extract_previews(raw_dir, preview_dir, raw_extensions, progress_callback=None):
    """Extracts the embedded JPEG preview from every RAW file in raw_dir that
    doesn't have one yet, so re-running a folder only does the new files.
    Returns how many previews were created.

    exiftool runs once per RAW extension (different bodies can need a
    different preview tag, so each is probed separately), batched over an
    explicit file list. progress_callback(done, total), if given, is polled
    every ~0.5s while exiftool runs - the only outside signal of progress is
    previews landing in preview_dir."""
    os.makedirs(preview_dir, exist_ok=True)
    by_stem, skipped = raw_files_by_stem(raw_dir, raw_extensions)
    for f in skipped:
        print(f"WARNING: skipping {f}: another RAW file with the same name already claims its preview/sidecar")

    todo = [(stem, f) for stem, f in sorted(by_stem.items()) if not _preview_ok(preview_dir, stem)]
    total = len(todo)
    if not todo:
        if progress_callback:
            progress_callback(0, 0)
        return 0

    # A failed earlier run can leave an empty .jpg behind; exiftool -w won't
    # write over an existing file.
    for stem, _ in todo:
        leftover = os.path.join(preview_dir, stem + ".jpg")
        if os.path.exists(leftover):
            os.remove(leftover)

    def count_done():
        present = set(os.listdir(preview_dir))
        return sum(1 for stem, _ in todo if stem + ".jpg" in present)

    groups = {}
    for stem, f in todo:
        groups.setdefault(_ext(f), []).append(f)

    for ext, files in groups.items():
        tag = find_preview_tag(os.path.join(raw_dir, files[0]))
        with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False) as argfile:
            argfile.write("\n".join(os.path.join(raw_dir, f) for f in files) + "\n")
        try:
            proc = subprocess.Popen(
                [
                    "exiftool", "-b", tag,
                    "-w", os.path.join(preview_dir, "%f.jpg"),
                    "-@", argfile.name,
                ]
            )
            while proc.poll() is None:
                if progress_callback:
                    progress_callback(count_done(), total)
                time.sleep(0.5)
        finally:
            os.unlink(argfile.name)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)

    if progress_callback:
        progress_callback(total, total)

    ensure_orientations(raw_dir, preview_dir, raw_extensions)

    missing = [f for stem, f in todo if not _preview_ok(preview_dir, stem)]
    for f in missing:
        print(f"WARNING: no embedded preview could be extracted from {f}")
    return total - len(missing)


def write_sidecar(raw_path, rating=None, reject=False, keywords=None, reject_reason=""):
    """Writes rating/keywords into an XMP sidecar. Uses fully-qualified tag
    names (XMP-xmp:Rating, XMP-dc:Subject) rather than the generic
    -Rating/-Keywords shortcuts, which can land in unexpected namespaces
    (e.g. pdf:Keywords) on a freshly created sidecar. Lightroom reads
    XMP-dc:Subject for its Keywords panel; there's no IPTC:Keywords tag
    here because IPTC-IIM has no home in a bare .xmp file - exiftool
    silently no-ops that write rather than erroring, so it's not worth
    carrying.

    rating/keywords are optional - pass None (or leave unset) to skip
    writing that field entirely, e.g. when the user only selected "rating"
    at triage time and left "keywords" unchecked.
    """
    xmp_path = os.path.splitext(raw_path)[0] + ".xmp"
    existed = os.path.exists(xmp_path)

    if not existed:
        # -o copies the RAW's embedded metadata into the new sidecar, which
        # includes its Camera Raw develop settings (crs: - just the camera
        # defaults, e.g. ColorTemperature/ToneCurve). Lightroom's "Read
        # Metadata from File" applies whatever develop settings a sidecar
        # holds, so those defaults reset every edit the photo has in the
        # catalog. They're stripped in the tag-writing step below (deleting
        # them in this same -o command doesn't take): a sidecar we create
        # carries no develop state. Pre-existing sidecars are never touched
        # - they may hold real edits Lightroom wrote itself.
        result = subprocess.run(
            ["exiftool", "-o", xmp_path, raw_path], capture_output=True, text=True
        )
        output = (result.stderr + result.stdout).strip()
        # exiftool refuses to -o over an existing file. "Already exists"
        # here means the sidecar was created between our exists() check and
        # exiftool running (an overlapping /write for the same folder, or a
        # stale SMB lookup cache) - the outcome we wanted, so carry on and
        # write the tags into it. We trust exiftool's own message rather
        # than re-checking the filesystem, since a stale cache could lie
        # twice. Any other failure is real.
        if result.returncode != 0 and "already exists" not in output:
            raise RuntimeError(f"couldn't create {os.path.basename(xmp_path)}: {output}")
        created = result.returncode == 0
    else:
        created = False

    tags = []
    if reject:
        tags = ["-XMP-xmp:Rating=-1", "-XMP-xmp:Label=Red"]
        if reject_reason:
            # Lightroom shows dc:Description as the Caption, so the reason
            # is visible (and searchable) next to the red label.
            tags.append(f"-XMP-dc:Description=Rejected: {reject_reason}")
    else:
        if rating is not None and rating != "":
            tags.append(f"-XMP-xmp:Rating={rating}")
        if keywords is not None and keywords != "":
            tags.append(f"-XMP-dc:Subject={keywords}")

    if created:
        tags.append("-XMP-crs:all=")

    if existed:
        # A sidecar from an earlier run can still carry that run's reject
        # markers (red label, "Rejected: ..." caption). A photo that's no
        # longer a reject - or is one for a new reason - must not inherit
        # them. Only clear what this app writes: a red label, or a caption
        # starting "Rejected:"; anything else was set in Lightroom.
        current = subprocess.run(
            ["exiftool", "-j", "-XMP-xmp:Label", "-XMP-dc:Description", xmp_path],
            capture_output=True, text=True,
        ).stdout
        info = (json.loads(current) or [{}])[0] if current.strip() else {}
        if not reject and info.get("Label") == "Red":
            tags.append("-XMP-xmp:Label=")
        if str(info.get("Description", "")).startswith("Rejected:") and not (reject and reject_reason):
            tags.append("-XMP-dc:Description=")

    if not tags:
        return

    subprocess.run(
        ["exiftool", "-overwrite_original"] + tags + [xmp_path], check=True
    )
