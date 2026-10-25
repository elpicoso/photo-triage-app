"""Merge the app's triage metadata into sidecars that Lightroom has since
re-saved (they now carry develop settings), WITHOUT overriding anything the
user set in Lightroom.

  merge_lightroom_metadata.py <folder> <new_backup> [--previous <old_backup>] [--apply]

  <folder>       the photos' folder (run it where exiftool can read it - the
                 Mac, against the photo volume, is much faster than the Pi's
                 SMB mount)
  <new_backup>   a copy of the folder's .xmp files made after the app wrote its
                 NEW results and BEFORE Lightroom's "Save Metadata to File"
  --previous     a copy of the sidecars from the app's PREVIOUS run. Needed when
                 the folder was imported into Lightroom before: the catalog
                 then still holds the old app values (old ratings, stale Red
                 labels), and without this they'd be mistaken for the user's own
  --apply        write (default is a dry run that prints the plan per photo)

Only sidecars containing real Lightroom develop data (crs:Version) are
considered; the rest still hold the app's output untouched. Develop (crs) data
is never touched.

Policy ("have" = the sidecar now, i.e. Lightroom's catalog):
  Rating       reject (-1) from the app is applied. Otherwise the app's rating is
               used when the sidecar has none, or - with --previous - when it
               still equals the old app's rating (or a stale reject). A different
               rating is the user's own and is kept.
  Label        Red follows the app (the app's reject marker): added for rejects,
               removed from photos that are no longer rejects. Any other label
               (Purple, ...) is kept.
  Description  an app caption ("Rejected: ...") is replaced by the new one (or
               cleared); a caption the user wrote is kept, and the app's is only
               added where there is none.
  Subject      the sidecar's keywords minus the OLD app's keywords (with
               --previous), plus the new app's; keywords you added stay.
"""
import json
import os
import subprocess
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
apply = "--apply" in sys.argv
previous = None
if "--previous" in sys.argv:
    i = sys.argv.index("--previous")
    previous = sys.argv[i + 1]
    args = [a for a in args if a != previous]
folder, backup = args[0], args[1]
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


def keyword_items(subject):
    """Individual keywords, also splitting an old single 'a, b, c' string."""
    items = []
    for s in subject:
        items.append(s.strip())
        items.extend(k.strip() for k in s.split(",") if k.strip())
    return items


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
    old = read(os.path.join(previous, name)) if previous and os.path.exists(os.path.join(previous, name)) else None
    out, notes = [], []

    # --- rating
    if want["rating"] == -1:
        if have["rating"] != -1:
            out.append("-XMP-xmp:Rating=-1")
            notes.append("rating -1 (reject)")
    elif want["rating"] is not None and have["rating"] != want["rating"]:
        stale = have["rating"] in (None, 0) or have["rating"] == -1 or (
            old is not None and have["rating"] == old["rating"]
        )
        if stale:
            out.append(f"-XMP-xmp:Rating={want['rating']}")
            notes.append(f"rating {have['rating'] or 0} -> {want['rating']} (app)")
        else:
            notes.append(f"KEPT your rating {have['rating']} (app had {want['rating']})")

    # --- label
    if want["label"] == "Red":
        if have["label"] in (None, "Red"):
            if have["label"] != "Red":
                out.append("-XMP-xmp:Label=Red")
                notes.append("label Red (reject)")
        else:
            notes.append(f"KEPT your label {have['label']} (app wanted Red)")
    elif have["label"] == "Red":
        out.append("-XMP-xmp:Label=")
        notes.append("removed stale Red label (no longer a reject)")
    elif have["label"]:
        notes.append(f"kept your label {have['label']}")

    # --- description
    have_d, want_d = have["description"], want["description"]
    if have_d and have_d.startswith("Rejected:"):
        if have_d != (want_d or ""):
            out.append(f"-XMP-dc:Description={want_d or ''}")
            notes.append("caption updated" if want_d else "stale reject caption removed")
    elif not have_d and want_d:
        out.append(f"-XMP-dc:Description={want_d}")
        notes.append("caption (app)")
    elif have_d and have_d != want_d:
        notes.append("kept your caption")

    # --- keywords
    old_items = {k.lower() for k in keyword_items(old["subject"])} if old else set()
    user_extra = [k for k in have["subject"] if k.lower() not in old_items and "," not in k]
    merged = list(want["subject"])
    lower = {k.lower() for k in merged}
    merged += [k for k in user_extra if k.lower() not in lower]
    if merged and merged != have["subject"]:
        out += ["-sep", "|||", "-XMP-dc:Subject=" + "|||".join(merged)]
        kept_user = len([k for k in merged if k.lower() not in {x.lower() for x in want["subject"]}])
        notes.append(f"keywords: {len(want['subject'])} from app" + (f" + {kept_user} of yours" if kept_user else ""))

    print(f"{name}: " + ("; ".join(notes) if notes else "nothing to change"))
    if out:
        touched += 1
        if apply:
            subprocess.run(["exiftool", "-overwrite_original", "-q", *out, cur_path], check=True)

print(("APPLIED" if apply else "DRY RUN"), "- sidecars changed:" if apply else "- would change:", touched)
