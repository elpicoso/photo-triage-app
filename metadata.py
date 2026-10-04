import os
import subprocess
import tempfile
import time
from collections import Counter


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

    if not os.path.exists(xmp_path):
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

    if not tags:
        return

    subprocess.run(
        ["exiftool", "-overwrite_original"] + tags + [xmp_path], check=True
    )
