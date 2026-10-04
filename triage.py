import base64
import io
import json
import os

import anthropic
from PIL import Image

from config import load_settings

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment


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
    instructions = []
    fields = []

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
        fields.append('"rating": 0-5')
    if do_keywords:
        instructions.append(f"- keywords: {keyword_criteria}")
        fields.append('"keywords": "..."')

    return f"""Review these {n} photos from {shoot_context}.

For each image, decide:
{chr(10).join(instructions)}

Respond with ONLY a JSON array, no other text, no markdown formatting.
Format: [{{"filename": "...", {", ".join(fields)}}}]

Filenames in order shown:
{filenames}
"""


def encode_image(path, max_dimension):
    """Downscale to max_dimension before sending to the API. Claude
    downsizes large images internally anyway, and some cameras embed
    full-resolution previews (5-10MB+ each) that otherwise blow past the
    API's request size limit once a batch is assembled."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def triage_batch(image_paths, do_reject=True, do_rating=True, do_keywords=True):
    settings = load_settings()
    content = []
    for path in image_paths:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": encode_image(path, settings["max_preview_dimension"]),
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

    response = client.messages.create(
        model=settings["triage_model"],
        max_tokens=2000,
        messages=[{"role": "user", "content": content}],
    )
    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(text)

    # Normalize so the CSV always has all three columns, even if a
    # given operation was skipped for this run.
    for row in parsed:
        row.setdefault("reject", "")
        row.setdefault("reject_reason", "")
        row.setdefault("rating", "")
        row.setdefault("keywords", "")
    return parsed


def triage_folder(
    preview_dir,
    batch_size=None,
    do_reject=True,
    do_rating=True,
    do_keywords=True,
    duplicate_groups=None,
    progress_callback=None,
    only=None,
):
    """only, if given, restricts the run to those preview filenames (the
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
                "reject": "",
                "reject_reason": "",
                "rating": "",
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
                triage_batch(batch, do_reject=do_reject, do_rating=do_rating, do_keywords=do_keywords)
            )
        except Exception as e:
            # Don't let one bad batch kill the whole run - flag the files
            # so they show up clearly in the review step instead.
            for path in batch:
                results.append(
                    {
                        "filename": os.path.basename(path),
                        "reject": "",
                        "reject_reason": "",
                        "rating": "",
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
                    "reject": "yes",
                    "reject_reason": f"Duplicate of {duplicate_of[fname]}",
                    "rating": "",
                    "keywords": "",
                    "duplicate_of": duplicate_of[fname],
                }
            )

    if progress_callback:
        progress_callback(total, total)

    results.sort(key=lambda r: r["filename"])
    return results
