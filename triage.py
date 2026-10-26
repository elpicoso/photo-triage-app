import base64
import io
import json
import os

import anthropic
from PIL import Image

from config import load_settings
from metadata import apply_orientation, read_orientations

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment

# The per-photo fields triage returns and the results CSV stores.
RESULT_FIELDS = ("filename", "subject", "reject", "reject_reason", "rating", "gallery", "keywords")


def build_prompt(
    n,
    filenames,
    do_reject,
    do_rating,
    do_keywords,
    shoot_context,
    reject_criteria,
    rating_criteria,
    keyword_criteria,
):
    """Builds the triage prompt with only the instructions/fields for
    operations the user actually selected, so we're not asking Claude
    (and paying for) fields we're going to throw away. shoot_context and
    the per-field criteria are all user-editable (Settings page) - only
    the field name prefixes and the JSON format below are fixed, so the
    response shape can't be broken by an edit."""
    instructions = [
        "- subject: first identify what this photo is - its subject and technique, "
        "in a few words (e.g. landscape, astrophotography, long exposure, "
        "architecture interior, street, portrait, wildlife, macro, low-light). "
        "Judge everything below against what that kind of photo calls for, not "
        "one universal standard: darkness is expected in astro and night shots, "
        "motion blur can be intentional in a long exposure, deep shadows can be "
        "deliberate in an interior."
    ]
    fields = ['"subject": "..."']

    if do_reject:
        instructions.append(f"- reject: {reject_criteria}")
        instructions.append(
            "- reject_reason: if reject is yes, a short phrase (under 10 words) "
            "saying specifically why; otherwise an empty string"
        )
        fields.append('"reject": "yes/no"')
        fields.append('"reject_reason": "..."')
    if do_rating:
        instructions.append(f"- rating: {rating_criteria}")
        instructions.append(
            '- gallery: a separate judgment from rating - would this photograph '
            'hang in a gallery? Judge the photograph, not how famous its '
            'subject is (a straight shot of a known artwork or landmark is '
            'documentation). Most photos are "no". "yes" only if the photo '
            "already has distinctive light, a strong composition, or a moment "
            "most people would not have captured (a light touch-up is fine). "
            '"with edits" only if one of those qualities is clearly there but '
            "held back by something you could name that a reasonable edit "
            "fixes (exposure, white balance, crop, straighten) - it can be a "
            'photo that rates only 3 as it stands. "no" for everything '
            "else: competent, generic, documentary or record shots, and "
            "anything you rejected."
        )
        fields.append('"rating": 0-5')
        fields.append('"gallery": "yes/with edits/no"')
    if do_keywords:
        instructions.append(f"- keywords: {keyword_criteria}")
        fields.append('"keywords": "..."')

    return f"""Review these {n} photos from {shoot_context}.

For each image, decide:
{chr(10).join(instructions)}

Respond with ONLY a JSON array, no other text, no markdown formatting.
Use exactly these fields and no others, and never use double quote
characters inside a text value (use single quotes or rephrase).
Format: [{{"filename": "...", {", ".join(fields)}}}]

Filenames in order shown:
{filenames}
"""


def encode_image(path, max_dimension, orientation=1):
    """Downscale to max_dimension before sending to the API. Claude
    downsizes large images internally anyway, and some cameras embed
    full-resolution previews (5-10MB+ each) that otherwise blow past the
    API's request size limit once a batch is assembled."""
    with Image.open(path) as img:
        img = apply_orientation(img.convert("RGB"), orientation)
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def triage_batch(image_paths, do_reject=True, do_rating=True, do_keywords=True, usage=None):
    settings = load_settings()
    content = []
    orientations = read_orientations(os.path.dirname(image_paths[0]))
    for path in image_paths:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": encode_image(
                        path,
                        settings["max_preview_dimension"],
                        orientations.get(os.path.splitext(os.path.basename(path))[0], 1),
                    ),
                },
            }
        )
    filenames = [os.path.basename(p) for p in image_paths]
    content.append(
        {
            "type": "text",
            "text": build_prompt(
                n=len(image_paths),
                filenames="\n".join(filenames),
                do_reject=do_reject,
                do_rating=do_rating,
                do_keywords=do_keywords,
                shoot_context=settings["shoot_context"],
                reject_criteria=settings["reject_criteria"],
                rating_criteria=settings["rating_criteria"],
                keyword_criteria=settings["keyword_criteria"],
            ),
        }
    )

    # A stronger model occasionally returns malformed JSON (an unescaped
    # quote inside a value) - the same request almost always parses on a
    # second try, so retry once before giving up on the batch.
    for attempt in (1, 2):
        response = client.messages.create(
            model=settings["triage_model"],
            max_tokens=2000,
            messages=[{"role": "user", "content": content}],
        )
        if usage is not None:
            # Every response is billed, including one whose JSON we then
            # have to retry.
            usage["requests"] = usage.get("requests", 0) + 1
            usage["input_tokens"] = usage.get("input_tokens", 0) + response.usage.input_tokens
            usage["output_tokens"] = usage.get("output_tokens", 0) + response.usage.output_tokens
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        try:
            # strict=False: a raw newline/tab inside a string value is
            # harmless here but fails strict parsing (and the retry too).
            parsed = json.loads(text, strict=False)
            break
        except ValueError:
            if attempt == 2:
                raise

    # Keep only the fields we store (a model may add extras like a
    # "gallery_note") and normalize so every row has every column, even if
    # a given operation was skipped for this run.
    #
    # Match results to the batch by filename and give every photo in it a
    # row: if the response skips or renames one, the CSV would miss that
    # preview, the folder would never reach "needs review", and the page
    # would offer "Run triage" again instead of Review.
    by_name = {
        row.get("filename"): row
        for row in parsed
        if isinstance(row, dict)
    }
    rows = []
    for name in filenames:
        row = by_name.get(name)
        if row is None:
            rows.append(
                {
                    **{field: "" for field in RESULT_FIELDS},
                    "filename": name,
                    "keywords": "ERROR: no result returned for this photo",
                }
            )
        else:
            rows.append({field: row.get(field, "") for field in RESULT_FIELDS})
    return rows


def triage_folder(
    preview_dir,
    batch_size=None,
    do_reject=True,
    do_rating=True,
    do_keywords=True,
    duplicate_groups=None,
    progress_callback=None,
    only=None,
    usage=None,
):
    """usage, if given, is a dict that accumulates the run's API usage
    (requests, input_tokens, output_tokens).

    only, if given, restricts the run to those preview filenames (the
    ones not already written - see app.pending_previews()), so re-running a
    folder doesn't re-triage photos that are done.

    duplicate_groups, if given, is dedupe.group_duplicates()'s output:
    {representative_filename: {"members": [...], ...}}. Every group member
    except the representative is skipped from Claude entirely (no API
    call, reject defaults to "yes") - the representative is the only one
    evaluated normally alongside whatever else isn't part of any group."""
    images = sorted(
        os.path.join(preview_dir, f)
        for f in os.listdir(preview_dir)
        if f.lower().endswith(".jpg") and (only is None or f in only)
    )
    total = len(images)

    duplicate_of = {}
    if duplicate_groups:
        for representative, info in duplicate_groups.items():
            for member in info["members"]:
                if member != representative:
                    duplicate_of[member] = representative

    if not (do_reject or do_rating or do_keywords):
        # Nothing selected - skip the API entirely, return empty rows
        # so the review table still lists every file.
        if progress_callback:
            progress_callback(total, total)
        return [
            {
                "filename": os.path.basename(p),
                "subject": "",
                "reject": "",
                "reject_reason": "",
                "rating": "",
                "gallery": "",
                "keywords": "",
                "duplicate_of": duplicate_of.get(os.path.basename(p), ""),
            }
            for p in images
        ]

    to_send = [p for p in images if os.path.basename(p) not in duplicate_of]
    batch_size = batch_size or load_settings()["batch_size"]
    results = []
    if progress_callback:
        progress_callback(0, total)
    for i in range(0, len(to_send), batch_size):
        batch = to_send[i : i + batch_size]
        try:
            results.extend(
                triage_batch(
                    batch,
                    do_reject=do_reject,
                    do_rating=do_rating,
                    do_keywords=do_keywords,
                    usage=usage,
                )
            )
        except Exception as e:
            # Don't let one bad batch kill the whole run - flag the files
            # so they show up clearly in the review step instead.
            for path in batch:
                results.append(
                    {
                        "filename": os.path.basename(path),
                        "subject": "",
                        "reject": "",
                        "reject_reason": "",
                        "rating": "",
                        "gallery": "",
                        "keywords": f"ERROR: {e}",
                    }
                )
        if progress_callback:
            # duplicates are skipped work, not pending work - count them as
            # already "done" so the bar doesn't stall short of 100%.
            progress_callback(len(results) + len(duplicate_of), total)

    for row in results:
        row.setdefault("duplicate_of", "")

    # Synthesize rows for skipped duplicates - no API call, reject=yes by
    # default (still overridable in the review step, same as any other row).
    for path in images:
        fname = os.path.basename(path)
        if fname in duplicate_of:
            results.append(
                {
                    "filename": fname,
                    "subject": "",
                    "reject": "yes",
                    "reject_reason": f"Duplicate of {duplicate_of[fname]}",
                    "rating": "",
                    "gallery": "",
                    "keywords": "",
                    "duplicate_of": duplicate_of[fname],
                }
            )

    if progress_callback:
        progress_callback(total, total)

    results.sort(key=lambda r: r["filename"])
    return results
