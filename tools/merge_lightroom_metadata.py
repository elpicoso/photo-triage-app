"""Merge the app's triage metadata back into sidecars that Lightroom has since
re-saved (they now carry develop settings), WITHOUT overriding anything the
user set in Lightroom.

  merge_lightroom_metadata.py <folder> <backup_dir>            dry run (prints the plan per photo)
  merge_lightroom_metadata.py <folder> <backup_dir> --apply    write

<backup_dir> is a copy of the folder's .xmp files made BEFORE Lightroom's
"Save Metadata to File" (see the README's workflow for already-edited photos).
Run it where exiftool can read the folder - the Mac, against the photo volume,
is much faster than going through the Pi's SMB mount.

Only sidecars that contain real Lightroom develop data (crs:Version) are
considered; the rest already hold the app's output untouched.

Policy (Lightroom's value wins whenever the user could have set one):
  Rating       reject (-1) from the app is applied; otherwise the app's rating is used
               only if the sidecar has none (0/unset). A different rating is kept.
  Label        the app's Red (reject) is applied only if there is no label or it is Red.
               Any other label (e.g. Purple) is kept; a non-reject never loses its label.
  Description  the app's caption is used only if the sidecar has none.
  Subject      union: the sidecar's keywords plus the app's, de-duplicated.
Develop (crs) data is never touched.
"""
import json
import os
import subprocess
import sys

folder, backup = sys.argv[1], sys.argv[2]
apply = "--apply" in sys.argv
FIELDS = ["-XMP-xmp:Rating", "-XMP-xmp:Label", "-XMP-dc:Description", "-XMP-dc:Subject", "-XMP-crs:Version"]


def read(path):
    out = subprocess.run(["exiftool", "-json", "-G1", "-s"] + FIELDS + [path],
                         capture_output=True, text=True).stdout
    row = json.loads(out)[0] if out.strip() else {}
    subject = row.get("XMP-dc:Subject")
    if isinstance(subject, str):
        subject = [subject]
    return {
        "rating": row.get("XMP-xmp:Rating"),
        "label": row.get("XMP-xmp:Label"),
        "description": row.get("XMP-dc:Description"),
        "subject": subject or [],
        "has_develop": row.get("XMP-crs:Version") is not None,
    }


names = sorted(f for f in os.listdir(backup) if f.endswith(".xmp") and not f.startswith("."))
touched = 0
for name in names:
    cur_path = os.path.join(folder, name)
    if not os.path.exists(cur_path):
        continue
    have = read(cur_path)
    if not have["has_develop"]:
        continue  # still the app's own sidecar - nothing to merge
    want = read(os.path.join(backup, name))
    args, notes = [], []

    if want["rating"] == -1:
        if have["rating"] != -1:
            args.append("-XMP-xmp:Rating=-1")
            notes.append("rating -1 (reject)")
    elif want["rating"] is not None:
        if have["rating"] in (None, 0):
            args.append(f"-XMP-xmp:Rating={want['rating']}")
            notes.append(f"rating {want['rating']} (app)")
        elif have["rating"] != want["rating"]:
            notes.append(f"KEPT your rating {have['rating']} (app had {want['rating']})")

    if want["label"] == "Red":
        if have["label"] in (None, "Red"):
            if have["label"] != "Red":
                args.append("-XMP-xmp:Label=Red")
                notes.append("label Red (reject)")
        else:
            notes.append(f"KEPT your label {have['label']} (app wanted Red)")
    elif have["label"]:
        notes.append(f"kept your label {have['label']}")

    if want["description"] and not have["description"]:
        args.append(f"-XMP-dc:Description={want['description']}")
        notes.append("caption (app)")
    elif have["description"] and have["description"] != want["description"]:
        notes.append("kept your caption")

    merged = list(have["subject"])
    lower = {k.lower() for k in merged}
    added = [k for k in want["subject"] if k.lower() not in lower]
    if added:
        merged += added
        args += ["-sep", "|||", "-XMP-dc:Subject=" + "|||".join(merged)]
        notes.append(f"keywords: kept {len(have['subject'])}, added {len(added)}")

    print(f"{name}: " + ("; ".join(notes) if notes else "nothing to change"))
    if args:
        touched += 1
        if apply:
            subprocess.run(["exiftool", "-overwrite_original", "-q", *args, cur_path], check=True)

print(("APPLIED" if apply else "DRY RUN"), "- Lightroom-saved sidecars changed:" if apply else "- would change:", touched)
