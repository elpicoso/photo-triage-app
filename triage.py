import base64
import json
import os

import anthropic

from config import TRIAGE_MODEL, BATCH_SIZE

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment

TRIAGE_PROMPT = """Review these {n} photos from a landscape/astrophotography shoot.

For each image, decide:
- reject: "yes" if technically bad (blurry, out of focus, blown highlights,
  poorly composed). Note: intentional long exposures, star trails, and
  motion blur in moving elements (water, clouds) are NOT rejects.
- rating: 1-5 stars if kept (use 0 if rejected)
- keywords: 5-8 specific, descriptive keywords, comma-separated
  (e.g. "Milky Way, granite boulders, long exposure" not "nature, sky")

Respond with ONLY a JSON array, no other text, no markdown formatting.
Format: [{{"filename": "...", "reject": "yes/no", "rating": 0-5, "keywords": "..."}}]

Filenames in order shown:
{filenames}
"""


def encode_image(path):
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def triage_batch(image_paths):
    content = []
    for path in image_paths:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": encode_image(path),
                },
            }
        )
    filenames = [os.path.basename(p) for p in image_paths]
    content.append(
        {
            "type": "text",
            "text": TRIAGE_PROMPT.format(
                n=len(image_paths), filenames="\n".join(filenames)
            ),
        }
    )

    response = client.messages.create(
        model=TRIAGE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": content}],
    )
    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def triage_folder(preview_dir, batch_size=None):
    batch_size = batch_size or BATCH_SIZE
    images = sorted(
        os.path.join(preview_dir, f)
        for f in os.listdir(preview_dir)
        if f.lower().endswith(".jpg")
    )

    results = []
    for i in range(0, len(images), batch_size):
        batch = images[i : i + batch_size]
        try:
            results.extend(triage_batch(batch))
        except Exception as e:
            # Don't let one bad batch kill the whole run - flag the files
            # so they show up clearly in the review step instead.
            for path in batch:
                results.append(
                    {
                        "filename": os.path.basename(path),
                        "reject": "no",
                        "rating": 0,
                        "keywords": f"ERROR: {e}",
                    }
                )
    return results
