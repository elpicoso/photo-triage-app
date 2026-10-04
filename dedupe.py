import os

from PIL import Image, ImageFilter, ImageStat


def compute_dhash(image_path, hash_size=8):
    """Difference hash: compares adjacent pixel brightness gradients rather
    than absolute brightness, so it stays stable across exposure/brightness
    shifts (e.g. a bracketed HDR sequence) while still changing for a
    genuinely different composition. Returns a hash_size*hash_size-bit int,
    comparable to another hash via hamming_distance()."""
    with Image.open(image_path) as img:
        img = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
        pixels = list(img.getdata())
    value = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            value = (value << 1) | (1 if pixels[offset + col] > pixels[offset + col + 1] else 0)
    return value


def hamming_distance(a, b):
    return bin(a ^ b).count("1")


def sharpness_score(image_path):
    """Edge-map variance normalized by the image's own overall pixel
    variance - higher means more in-focus detail relative to the image's
    contrast, which stays meaningful across a same-exposure continuous-
    shooting burst (the common case). Raw edge variance alone is skewed by
    exposure/contrast differences (a brighter or higher-contrast frame
    scores "sharper" even at identical focus), so this is only a
    best-effort proxy across a wide exposure bracket - it's a starting
    guess for which member of a duplicate group to send to Claude, not a
    final judgment; the review page's thumbnails and "use this instead"
    swap exist specifically so a bad pick here is easy to catch and fix."""
    with Image.open(image_path) as img:
        gray = img.convert("L")
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_variance = ImageStat.Stat(edges).var[0]
        own_variance = ImageStat.Stat(gray).var[0]
        return edge_variance / (own_variance + 1e-6)


def group_duplicates(preview_dir, threshold, only=None):
    """Groups near-duplicate previews via pairwise dHash distance (Union-Find
    over pairs within threshold), then within each group of size > 1 picks
    the sharpest member as the representative - the only one that should be
    sent to Claude for full triage.

    only, if given, restricts grouping to those filenames.

    Returns {representative_filename: {"members": [...], "sharpness": {filename: score}}}
    for every group with more than one member. Filenames not in any key's
    "members" list are not part of a duplicate group."""
    filenames = sorted(
        f
        for f in os.listdir(preview_dir)
        if f.lower().endswith(".jpg") and (only is None or f in only)
    )
    hashes = {f: compute_dhash(os.path.join(preview_dir, f)) for f in filenames}

    parent = {f: f for f in filenames}

    def find(f):
        while parent[f] != f:
            parent[f] = parent[parent[f]]
            f = parent[f]
        return f

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(filenames):
        for b in filenames[i + 1 :]:
            if hamming_distance(hashes[a], hashes[b]) <= threshold:
                union(a, b)

    clusters = {}
    for f in filenames:
        clusters.setdefault(find(f), []).append(f)

    groups = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        scores = {m: sharpness_score(os.path.join(preview_dir, m)) for m in members}
        representative = max(members, key=lambda m: scores[m])
        groups[representative] = {"members": members, "sharpness": scores}
    return groups
