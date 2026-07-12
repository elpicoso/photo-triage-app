import csv
import os

from flask import Flask, render_template, request, redirect, url_for, abort

from config import RAW_ROOT, RAW_EXTENSIONS
from metadata import extract_previews, write_sidecar
from triage import triage_folder

app = Flask(__name__)


def safe_path(subpath):
    """Resolve subpath under RAW_ROOT and refuse to escape it (e.g. via ../)."""
    target = os.path.normpath(os.path.join(RAW_ROOT, subpath))
    root = os.path.normpath(RAW_ROOT)
    if not (target == root or target.startswith(root + os.sep)):
        abort(400)
    return target


def preview_dir(folder):
    return os.path.join(safe_path(folder), "_previews")


def csv_path(folder):
    return os.path.join(preview_dir(folder), "triage_results.csv")


def written_marker(folder):
    return os.path.join(preview_dir(folder), ".written")


def get_status(folder):
    if os.path.exists(written_marker(folder)):
        return "written"
    if os.path.exists(csv_path(folder)):
        return "triaged - needs review"
    if os.path.isdir(preview_dir(folder)):
        return "extracted"
    return "not started"


def is_shoot_folder(path):
    """True if this folder directly contains RAW files."""
    try:
        for f in os.listdir(path):
            if "." in f and f.rsplit(".", 1)[1].lower() in RAW_EXTENSIONS:
                return True
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        pass
    return False


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

    return render_template(
        "browse.html",
        subpath=subpath,
        breadcrumbs=breadcrumbs,
        subdirs=subdir_entries,
        is_shoot=is_shoot,
        status=status,
    )


@app.route("/help")
def help_page():
    return render_template("help.html")


@app.route("/extract/<path:folder>", methods=["POST"])
def extract(folder):
    raw_dir = safe_path(folder)
    raw_ext = request.form.get("raw_ext", "CR2")
    extract_previews(raw_dir, preview_dir(folder), raw_ext)
    return redirect(url_for("browse", subpath=folder))


@app.route("/triage/<path:folder>", methods=["POST"])
def triage(folder):
    results = triage_folder(preview_dir(folder))
    with open(csv_path(folder), "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["filename", "reject", "rating", "keywords"]
        )
        writer.writeheader()
        writer.writerows(results)
    return redirect(url_for("review", folder=folder))


@app.route("/review/<path:folder>")
def review(folder):
    with open(csv_path(folder), newline="") as f:
        rows = list(csv.DictReader(f))
    return render_template("review.html", folder=folder, rows=rows)


@app.route("/write/<path:folder>", methods=["POST"])
def write(folder):
    raw_dir = safe_path(folder)
    raw_ext = request.form.get("raw_ext", "CR2")

    filenames = request.form.getlist("filename")
    rejects = request.form.getlist("reject")
    ratings = request.form.getlist("rating")
    keywords_list = request.form.getlist("keywords")

    for filename, reject, rating, keywords in zip(
        filenames, rejects, ratings, keywords_list
    ):
        raw_path = os.path.join(raw_dir, filename.replace(".jpg", f".{raw_ext}"))
        if not os.path.exists(raw_path):
            continue
        write_sidecar(
            raw_path,
            rating=rating,
            reject=reject.strip().lower() == "yes",
            keywords=keywords,
        )

    open(written_marker(folder), "w").close()
    return redirect(url_for("browse", subpath=folder))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
