import os
import subprocess
import time


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


def extract_previews(raw_dir, preview_dir, raw_ext, progress_callback=None):
    """progress_callback(done, total), if given, is polled every ~0.5s while
    exiftool runs - it processes the whole directory in one batched call, so
    the only outside signal of progress is counting .jpg files as they land
    in preview_dir."""
    os.makedirs(preview_dir, exist_ok=True)
    raw_files = [
        f for f in os.listdir(raw_dir) if f.lower().endswith(raw_ext.lower())
    ]
    if not raw_files:
        return 0

    total = len(raw_files)
    tag = find_preview_tag(os.path.join(raw_dir, raw_files[0]))
    proc = subprocess.Popen(
        [
            "exiftool", "-b", tag,
            "-w", os.path.join(preview_dir, "%f.jpg"),
            "-ext", raw_ext,
            raw_dir,
        ]
    )
    while proc.poll() is None:
        if progress_callback:
            done = sum(
                1 for f in os.listdir(preview_dir) if f.lower().endswith(".jpg")
            )
            progress_callback(min(done, total), total)
        time.sleep(0.5)
    if progress_callback:
        progress_callback(total, total)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args)
    return total


def write_sidecar(raw_path, rating=None, reject=False, keywords=None):
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
