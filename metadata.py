import os
import subprocess


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


def extract_previews(raw_dir, preview_dir, raw_ext):
    os.makedirs(preview_dir, exist_ok=True)
    raw_files = [
        f for f in os.listdir(raw_dir) if f.lower().endswith(raw_ext.lower())
    ]
    if not raw_files:
        return 0

    tag = find_preview_tag(os.path.join(raw_dir, raw_files[0]))
    subprocess.run(
        [
            "exiftool", "-b", tag,
            "-w", os.path.join(preview_dir, "%f.jpg"),
            "-ext", raw_ext,
            raw_dir,
        ],
        check=True,
    )
    return len(raw_files)


def write_sidecar(raw_path, rating, reject, keywords):
    """Writes rating/keywords into an XMP sidecar. Uses fully-qualified tag
    names (XMP-xmp:Rating, XMP-dc:Subject, IPTC:Keywords) rather than the
    generic -Rating/-Keywords shortcuts, which can land in unexpected
    namespaces (e.g. pdf:Keywords) on a freshly created sidecar."""
    xmp_path = os.path.splitext(raw_path)[0] + ".xmp"

    if not os.path.exists(xmp_path):
        subprocess.run(["exiftool", "-o", xmp_path, raw_path], check=True)

    if reject:
        tags = ["-XMP-xmp:Rating=-1", "-XMP-xmp:Label=Red"]
    else:
        tags = [
            f"-XMP-xmp:Rating={rating}",
            f"-XMP-dc:Subject={keywords}",
            f"-IPTC:Keywords={keywords}",
        ]

    subprocess.run(
        ["exiftool", "-overwrite_original"] + tags + [xmp_path], check=True
    )
