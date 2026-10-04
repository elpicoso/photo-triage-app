import csv
import json
import os
import threading
from collections import Counter

from flask import Flask, render_template, request, redirect, url_for, abort, send_file

from config import load_settings, save_settings
from dedupe import group_duplicates
from metadata import extract_previews, write_sidecar
from triage import triage_folder

app = Flask(__name__)


def safe_path(subpath):
    """Resolve subpath under raw_root and refuse to escape it (e.g. via ../)."""
    raw_root = load_settings()["raw_root"]
    target = os.path.normpath(os.path.join(raw_root, subpath))
    root = os.path.normpath(raw_root)
    if not (target == root or target.startswith(root + os.sep)):
        abort(400)
    return target


def preview_dir(folder):
    return os.path.join(safe_path(folder), "_previews")


def csv_path(folder):
    return os.path.join(preview_dir(folder), "triage_results.csv")


def ops_path(folder):
    return os.path.join(preview_dir(folder), ".ops.json")


def duplicates_path(folder):
    return os.path.join(preview_dir(folder), ".duplicates.json")


def written_marker(folder):
    return os.path.join(preview_dir(folder), ".written")


def progress_path(folder):
    return os.path.join(preview_dir(folder), ".progress.json")


def write_progress(folder, stage, done, total, error=None):
    """Written by the background extract/triage thread, polled by /status.
    Written to a temp file then renamed so a concurrent GET never sees a
    half-written JSON file."""
    data = {"stage": stage, "done": done, "total": total}
    if error:
        data["error"] = error
    path = progress_path(folder)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def clear_progress(folder):
    try:
        os.remove(progress_path(folder))
    except FileNotFoundError:
        pass


def read_progress(folder):
    path = progress_path(folder)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def is_active(folder):
    """True if a background extract/triage is actually running - an error
    state leaves the progress file behind so the UI can show it, but
    shouldn't block starting a fresh attempt."""
    progress = read_progress(folder)
    return progress is not None and "error" not in progress


def get_status(folder):
    if os.path.exists(written_marker(folder)):
        return "written"
    if os.path.exists(csv_path(folder)):
        return "triaged - needs review"
    if os.path.isdir(preview_dir(folder)):
        return "extracted"
    return "not started"


def detect_raw_ext(path):
    """Look at what's actually on disk rather than trusting a hardcoded
    default - a folder full of .CR3 files with a form defaulting to "CR2"
    causes /write to silently skip every row, since raw_path then never
    matches a real file. Picks the most common RAW extension present."""
    raw_extensions = load_settings()["raw_extensions"]
    try:
        counts = Counter(
            f.rsplit(".", 1)[1].upper()
            for f in os.listdir(path)
            if "." in f and f.rsplit(".", 1)[1].lower() in raw_extensions
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return None
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def is_shoot_folder(path):
    """True if this folder directly contains RAW files."""
    return detect_raw_ext(path) is not None


def api_key_configured():
    """ANTHROPIC_API_KEY is env-var-only (never in settings.json - see
    Settings page docstring), so a missing/empty value can't be detected
    via load_settings(). Without this check, a fresh install with no key
    set just runs triage and gets a cryptic "ERROR: ..." per row instead
    of a clear signal that setup isn't finished."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@app.route("/")
@app.route("/browse/")
@app.route("/browse/<path:subpath>")
def browse(subpath=""):
    current_dir = safe_path(subpath)
    if not os.path.isdir(current_dir):
        abort(404)

    subdirs = sorted(
        f
        for f in os.listdir(current_dir)
        if os.path.isdir(os.path.join(current_dir, f))
        and not f.startswith(".")
        and not f.startswith("_")
    )
    subdir_entries = [
        {"name": f, "path": f"{subpath}/{f}" if subpath else f} for f in subdirs
    ]

    breadcrumbs = []
    if subpath:
        parts = subpath.split("/")
        accum = ""
        for part in parts:
            accum = f"{accum}/{part}" if accum else part
            breadcrumbs.append({"name": part, "path": accum})

    is_shoot = bool(subpath) and is_shoot_folder(current_dir)
    status = get_status(subpath) if is_shoot else None
    progress = read_progress(subpath) if is_shoot else None

    return render_template(
        "browse.html",
        subpath=subpath,
        breadcrumbs=breadcrumbs,
        subdirs=subdir_entries,
        is_shoot=is_shoot,
        status=status,
        progress=progress,
        raw_root=load_settings()["raw_root"],
        api_key_configured=api_key_configured(),
    )


@app.route("/status/<path:folder>")
def status_route(folder):
    data = {"status": get_status(folder), "active": False}
    progress = read_progress(folder)
    if progress is not None:
        data["active"] = "error" not in progress
        data.update(progress)
    return data


@app.route("/help")
def help_page():
    return render_template("help.html")


@app.route("/settings/browse")
def settings_browse():
    """Read-only directory listing for the raw_root folder picker. Deliberately
    not scoped by safe_path() - that helper resolves paths under the
    already-configured raw_root, but this endpoint exists to let you pick a
    new raw_root in the first place."""
    path = os.path.normpath(request.args.get("path") or "/")
    if not os.path.isdir(path):
        path = "/"
    try:
        entries = sorted(
            f
            for f in os.listdir(path)
            if os.path.isdir(os.path.join(path, f)) and not f.startswith(".")
        )
    except PermissionError:
        entries = []
    parent = os.path.dirname(path) if path != os.path.dirname(path) else None
    return {
        "path": path,
        "parent": parent,
        "entries": [{"name": e, "path": os.path.join(path, e)} for e in entries],
    }


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        form = request.form
        try:
            try:
                batch_size = int(form.get("batch_size", ""))
                max_preview_dimension = int(form.get("max_preview_dimension", ""))
                duplicate_threshold = int(form.get("duplicate_threshold", ""))
            except ValueError:
                raise ValueError(
                    "Batch size, max preview dimension, and duplicate threshold "
                    "must be whole numbers"
                )

            new_settings = {
                "raw_root": form.get("raw_root", "").strip(),
                "triage_model": form.get("triage_model", "").strip(),
                "batch_size": batch_size,
                "max_preview_dimension": max_preview_dimension,
                "raw_extensions": [
                    e.strip().lower()
                    for e in form.get("raw_extensions", "").split(",")
                    if e.strip()
                ],
                "shoot_context": form.get("shoot_context", "").strip(),
                "reject_criteria": form.get("reject_criteria", "").strip(),
                "rating_criteria": form.get("rating_criteria", "").strip(),
                "keyword_criteria": form.get("keyword_criteria", "").strip(),
                "duplicate_threshold": duplicate_threshold,
            }
            if not new_settings["raw_root"] or not new_settings["triage_model"]:
                raise ValueError("RAW root and triage model can't be empty")
            if batch_size < 1:
                raise ValueError("Batch size must be a positive number")
            if max_preview_dimension < 1:
                raise ValueError("Max preview dimension must be a positive number")
            if not (0 <= duplicate_threshold <= 64):
                raise ValueError("Duplicate threshold must be between 0 and 64")
            if not new_settings["raw_extensions"]:
                raise ValueError("At least one RAW extension is required")
            if not new_settings["shoot_context"]:
                raise ValueError("Shoot context can't be empty")
            if not (
                new_settings["reject_criteria"]
                and new_settings["rating_criteria"]
                and new_settings["keyword_criteria"]
            ):
                raise ValueError(
                    "Reject, rating, and keyword criteria can't be empty - Claude "
                    "needs instructions for any field it's asked to fill in"
                )
        except ValueError as e:
            return render_template(
                "settings.html", values=form, error=str(e), saved=None
            )

        save_settings(new_settings)
        return redirect(url_for("settings_page", saved=1))

    current = load_settings()
    values = {
        "raw_root": current["raw_root"],
        "triage_model": current["triage_model"],
        "batch_size": current["batch_size"],
        "max_preview_dimension": current["max_preview_dimension"],
        "raw_extensions": ", ".join(sorted(current["raw_extensions"])),
        "shoot_context": current["shoot_context"],
        "reject_criteria": current["reject_criteria"],
        "rating_criteria": current["rating_criteria"],
        "keyword_criteria": current["keyword_criteria"],
        "duplicate_threshold": current["duplicate_threshold"],
    }
    return render_template(
        "settings.html", values=values, saved=request.args.get("saved"), error=None
    )


@app.route("/extract/<path:folder>", methods=["POST"])
def extract(folder):
    if is_active(folder):
        return redirect(url_for("browse", subpath=folder))

    raw_dir = safe_path(folder)
    raw_ext = detect_raw_ext(raw_dir)
    if raw_ext is None:
        abort(400, "No RAW files found in this folder")

    os.makedirs(preview_dir(folder), exist_ok=True)
    write_progress(folder, "extracting", 0, 0)

    def run():
        try:
            extract_previews(
                raw_dir,
                preview_dir(folder),
                raw_ext,
                progress_callback=lambda done, total: write_progress(
                    folder, "extracting", done, total
                ),
            )
        except Exception as e:
            write_progress(folder, "extracting", 0, 0, error=str(e))
            return
        clear_progress(folder)

    threading.Thread(target=run, daemon=True).start()
    return redirect(url_for("browse", subpath=folder))


@app.route("/triage/<path:folder>", methods=["POST"])
def triage(folder):
    if is_active(folder):
        return redirect(url_for("browse", subpath=folder))

    selected_ops = request.form.getlist("ops")
    do_reject = "reject" in selected_ops
    do_rating = "rating" in selected_ops
    do_keywords = "keywords" in selected_ops
    do_duplicates = "duplicates" in selected_ops

    write_progress(folder, "triaging", 0, 0)

    def run():
        try:
            duplicate_groups = None
            if do_duplicates:
                threshold = load_settings()["duplicate_threshold"]
                duplicate_groups = group_duplicates(preview_dir(folder), threshold)
                with open(duplicates_path(folder), "w") as f:
                    json.dump(duplicate_groups, f)
            elif os.path.exists(duplicates_path(folder)):
                # A previous run found groups but this run doesn't want
                # dedup applied - clear stale grouping data so the review
                # page doesn't show groups that no longer apply.
                os.remove(duplicates_path(folder))

            results = triage_folder(
                preview_dir(folder),
                do_reject=do_reject,
                do_rating=do_rating,
                do_keywords=do_keywords,
                duplicate_groups=duplicate_groups,
                progress_callback=lambda done, total: write_progress(
                    folder, "triaging", done, total
                ),
            )
        except Exception as e:
            write_progress(folder, "triaging", 0, 0, error=str(e))
            return

        with open(csv_path(folder), "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["filename", "reject", "rating", "keywords", "duplicate_of"],
            )
            writer.writeheader()
            writer.writerows(results)

        # Remember what was selected so the review step can default its own
        # write-time checkboxes to match (with the option to override there).
        with open(ops_path(folder), "w") as f:
            json.dump(
                {
                    "reject": do_reject,
                    "rating": do_rating,
                    "keywords": do_keywords,
                    "duplicates": do_duplicates,
                },
                f,
            )

        clear_progress(folder)

    threading.Thread(target=run, daemon=True).start()
    return redirect(url_for("browse", subpath=folder))


@app.route("/review/<path:folder>")
def review(folder):
    with open(csv_path(folder), newline="") as f:
        rows = list(csv.DictReader(f))

    ops = {"reject": True, "rating": True, "keywords": True}
    if os.path.exists(ops_path(folder)):
        with open(ops_path(folder)) as f:
            ops.update(json.load(f))

    # Attach group info (for the thumbnail/badge/swap UI) to each row that's
    # part of a duplicate group - None for rows that aren't.
    group_by_filename = {}
    if os.path.exists(duplicates_path(folder)):
        with open(duplicates_path(folder)) as f:
            duplicate_groups = json.load(f)
        for representative, info in duplicate_groups.items():
            for member in info["members"]:
                group_by_filename[member] = {
                    "representative": representative,
                    "members": info["members"],
                    "sharpness": round(info["sharpness"][member], 1),
                }
    for row in rows:
        row["group"] = group_by_filename.get(row["filename"])

    progress = read_progress(folder)
    writing = progress if is_active(folder) and progress["stage"] == "writing" else None

    return render_template(
        "review.html", folder=folder, rows=rows, ops=ops, writing=writing
    )


@app.route("/preview/<path:folder>/<filename>")
def preview_image(folder, filename):
    """Serves a single extracted preview JPEG for the <img> thumbnails on
    the review page. Not scoped by safe_path() (that resolves RAW-folder
    paths) - instead resolves directly under this folder's own preview
    directory and refuses to escape it, same pattern as safe_path()."""
    pdir = preview_dir(folder)
    target = os.path.normpath(os.path.join(pdir, filename))
    if not (target == pdir or target.startswith(pdir + os.sep)) or not os.path.isfile(target):
        abort(404)
    return send_file(target, mimetype="image/jpeg")


@app.route("/write/<path:folder>", methods=["POST"])
def write(folder):
    # Writing a whole folder is slow over SMB (a couple of exiftool calls
    # per photo - minutes for a big shoot), so it runs in the background
    # like extract/triage. A second submit while one is running is ignored
    # rather than racing the first over the same sidecars.
    if is_active(folder):
        return redirect(url_for("browse", subpath=folder))

    raw_dir = safe_path(folder)
    raw_ext = detect_raw_ext(raw_dir)
    if raw_ext is None:
        abort(400, "No RAW files found in this folder")

    write_ops = request.form.getlist("ops")
    do_rating = "rating" in write_ops
    do_keywords = "keywords" in write_ops

    # Copy everything out of the request now - the thread outlives it.
    jobs = []
    for filename, reject, rating, keywords in zip(
        request.form.getlist("filename"),
        request.form.getlist("reject"),
        request.form.getlist("rating"),
        request.form.getlist("keywords"),
    ):
        raw_path = os.path.join(raw_dir, filename.replace(".jpg", f".{raw_ext}"))
        if not os.path.exists(raw_path):
            continue
        jobs.append(
            (
                filename,
                raw_path,
                rating if do_rating else None,
                reject.strip().lower() == "yes",
                keywords if do_keywords else None,
            )
        )

    total = len(jobs)
    write_progress(folder, "writing", 0, total)

    def run():
        try:
            for done, (filename, raw_path, rating, reject, keywords) in enumerate(jobs):
                write_sidecar(raw_path, rating=rating, reject=reject, keywords=keywords)
                write_progress(folder, "writing", done + 1, total)
        except Exception as e:
            write_progress(folder, "writing", done, total, error=f"{filename}: {e}")
            return

        open(written_marker(folder), "w").close()
        clear_progress(folder)

    threading.Thread(target=run, daemon=True).start()
    return redirect(url_for("browse", subpath=folder))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
