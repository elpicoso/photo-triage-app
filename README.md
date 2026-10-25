# Photo Triage

A small self-hosted Flask app that triages a backlog of RAW files: it pulls
the embedded JPEG preview out of each RAW file, sends batches of previews to
the Claude API for reject/rating/keyword suggestions, lets you review and
edit the results in a browser, then writes your (possibly edited) choices
into XMP sidecar files that Lightroom Classic (or any XMP-aware catalog
tool) can pick up.

Claude first identifies each photo's subject and technique (landscape,
astro, interior, street, ...) and judges it against what that kind of
photo calls for, and the evaluation criteria are fully editable from the Settings page — see
[Customizing the evaluation criteria](#customizing-the-evaluation-criteria)
below if you shoot something else.

Each install is independent — there's no shared server or account system.
You run your own copy against your own photos with your own Anthropic API
key.

## Requirements

- Python 3.9+
- [`exiftool`](https://exiftool.org/) on the machine running the app (it's
  what actually reads/writes the RAW files and XMP sidecars)
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
  — billed separately from any Claude.ai subscription, pay-as-you-go per
  API call

This is commonly run on a Raspberry Pi or other always-on machine on your
network, with the photos themselves living elsewhere (e.g. a Mac) and
mounted in over SMB — see [Optional: mounting a remote photo
share](#optional-mounting-a-remote-photo-share) below. It works just as
well pointed directly at a local folder.

## Quick start

```bash
# install exiftool - Debian/Ubuntu/Raspberry Pi OS:
sudo apt update && sudo apt install exiftool -y
# macOS: brew install exiftool

git clone <this repo> photo-triage-app
cd photo-triage-app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY="your-key-here"
python3 app.py
```

Visit `http://<the machine's IP>:5000` from any browser on your network.
If `ANTHROPIC_API_KEY` isn't set, the app still runs and lets you browse
folders, but shows a warning banner since triage will fail without it.

### Or with Docker

Skips installing Python/exiftool on the host entirely - both are bundled
in the image.

```bash
git clone <this repo> photo-triage-app
cd photo-triage-app

touch settings.json   # so Docker mounts it as a file, not a directory
echo 'ANTHROPIC_API_KEY=your-key-here' > .env

# edit docker-compose.yml first: point the /path/to/your/photos volume
# at your actual photo directory
docker compose up -d --build
```

Visit `http://<the machine's IP>:5000`, same as above. `settings.json`
persists in the repo directory across rebuilds since it's bind-mounted
in (see `docker-compose.yml`).

## Configuration

Everything below is editable from the **Settings** page in the browser
(`/settings`) — no code changes or restarts needed, changes apply on the
next request. Each setting also has an environment-variable fallback,
used only until the Settings page is saved for the first time (after
that, `settings.json` next to `app.py` takes over — it's gitignored, so
each install/machine keeps its own):

| Setting | Env var fallback | Default |
| --- | --- | --- |
| RAW root (top of the browsable photo tree) | `RAW_ROOT` | `/mnt/photos` |
| RAW extensions (used to auto-detect shoot folders) | — | `cr2, cr3, nef, arw, raf, dng, orf, rw2` |
| Triage model (Anthropic model ID) | `TRIAGE_MODEL` | `claude-haiku-4-5-20251001` |
| Batch size (images per API call) | `BATCH_SIZE` | `8` |
| Max preview dimension (px, before sending to the API) | `MAX_PREVIEW_DIMENSION` | `1568` |
| Shoot context, reject/rating/keyword criteria | — | genre-neutral defaults (Claude identifies each photo's subject/technique first) |
| Duplicate threshold (Hamming distance, 0-64) | `DUPLICATE_THRESHOLD` | `5` |

Saving the Settings page stores every field in `settings.json`, so later
changes to the built-in defaults won't reach an install that has saved
once. Each setting shows a **Reset to default** link when it differs from
the default, and **Reset all to defaults** fills the whole form (nothing is
saved until you click Save).

`ANTHROPIC_API_KEY` is deliberately **not** on the Settings page — it's a
secret, and the Settings page is reachable by anyone who can reach the
app on your network. Set it as an environment variable only (see
[Running it permanently](#running-it-permanently-optional) below for the
systemd version).

RAW file types are never set per folder — every file in a folder whose
extension is in the RAW extensions list is picked up, so a folder shot
with two camera bodies (say `.CR3` and `.CR2`) is handled in one pass.

## Using it

1. Browse to a folder that directly contains RAW files — the app shows a
   card with a status and an action button.
2. **Extract previews** — pulls the embedded JPEG preview out of each RAW
   file via `exiftool`. Runs in the background with a live progress bar;
   no need to watch logs or refresh. It only extracts files that don't
   have a preview yet: if more RAW files appear in a folder you've
   already processed, the folder card offers **Extract the rest**, and
   triage, review, and write then cover only those new photos — photos
   already written are never re-triaged or re-written.
3. **Run triage** — choose which of Flag rejects / Rate / Keyword / Detect
   duplicates you want for this folder, then sends batches of preview
   JPEGs to the Claude API. Also runs in the background with a progress
   bar. If a batch fails (network hiccup, rate limit), the folder's
   progress panel shows the error with a **Try again** button instead of
   hanging. If Detect duplicates is checked, near-identical burst shots
   are grouped locally first (free, no API call) and only the sharpest
   frame per group is sent to Claude — the rest are pre-flagged as
   rejects.
   When Rate is checked, Claude also makes a separate, stricter call on
   whether each photo is gallery material (independent of the star
   rating): **yes** (as shot), **with edits** (strong subject/light that a
   reasonable edit would carry), or **no** (generic or documentary). On
   write, that becomes the keyword `gallery-candidate` or
   `gallery-with-edits`, so you can filter or build a Smart Collection in
   Lightroom.
4. **Review results** — an editable table of every file's suggested
   reject/rating/keywords. Fix anything before writing — this is the
   checkpoint that catches anything Claude got wrong. Rows in a duplicate
   group show a thumbnail, a sharpness score, and which one was
   auto-picked as "kept" — click **Use this instead** on any other member
   to swap which one gets kept before writing. The sharpness pick is a
   best-effort heuristic (most reliable within a same-exposure burst,
   less so across a wide exposure bracket), which is exactly why this
   swap exists — don't trust it blindly on anything that matters.
5. **Write to XMP** — writes your (possibly edited) choices into XMP
   sidecar files next to each RAW, using fully-qualified tag names
   (`XMP-xmp:Rating`, `XMP-dc:Subject`) so they land where Lightroom
   actually looks for them. You can uncheck Rating or Keywords here to
   skip writing that field for this batch. A rejected row always gets
   `Rating=-1`, a **red color label**, and a Caption (`XMP-dc:Description`)
   of "Rejected: <reason>" - the reason Claude gave, editable in the
   review table's "Why rejected" column. Lightroom Classic does not turn
   `Rating=-1` into its Reject flag (flags live only in its catalog), so
   the red label is what marks rejects there: filter by Color Label → Red,
   check them, select all, and press `X` to flag them as Rejected.
   To run a folder through again from scratch (new criteria, a fixed
   bug), use **Reprocess folder** on its card: it forgets the triage results
   and which photos were written, so everything goes through triage, review
   and write again. Previews and existing sidecars are kept until you write.
   The folder list also shows a status tag on every folder that has been
   started (written, needs review, ...).
6. In Lightroom Classic: **back up the catalog first** (File → Back Up
   Catalog, or let it back up on quit), then select the photos and use
   **Metadata → Read Metadata from File**. This step is manual -
   "Synchronize Folder" alone won't pick up changed sidecars on photos
   already in the catalog, only added/removed files. Lightroom keeps your
   edits in its catalog, not in the sidecar, and this command makes the
   sidecar the source of truth: it overwrites the catalog's metadata with
   what's on disk. The sidecars this app creates contain only the rating,
   reject label, caption and keywords - no develop settings - but try it on
   **one photo you've already edited** first and confirm its edits survive
   before running it on a whole folder.

### Photos you've already edited in Lightroom

Lightroom keeps edits in its catalog, and a sidecar the app creates contains
no develop settings. If you run **Read Metadata from File** over photos that
already have edits, Lightroom can treat the sidecar as the truth and lose
them. Use this order instead (back up the catalog first, and try it on one
photo):

1. Copy the folder's `.xmp` files somewhere safe (a "before" backup).
2. In Lightroom, select the photos and run **Metadata → Save Metadata to
   File**. If it asks about metadata changed by another application, choose
   **Overwrite Settings** (writes the catalog to disk). This puts your edits
   into the sidecars - but also replaces the app's ratings/keywords with the
   catalog's.
3. Run `tools/merge_lightroom_metadata.py <folder> <backup-dir>` (dry run,
   then `--apply`). If the folder was imported into Lightroom before, also
   pass `--previous <old-backup-dir>` - a copy of the sidecars from the app's
   previous run - so the old app ratings and Red labels still sitting in the
   catalog aren't mistaken for your own. Add `--keep-rating name1,name2` to
   keep your current rating on specific photos (for example the ones you've
   edited heavily - the dry run's plan shows old -> new for each). It puts the app's ratings, red reject labels, captions
   and keywords back **without overriding anything you set in Lightroom**
   (your ratings, other color labels and captions win; keywords are merged),
   and never touches develop settings.
4. Now **Read Metadata from File**. If it asks, choose **Import Settings
   from Disk** - the sidecars contain your edits, so nothing is reset.

Photos with no edits can skip all of this.

**Be careful with Lightroom's *Catalog Settings → Metadata → Automatically
write changes into XMP*.** It does keep sidecars carrying your edits, but the
moment it is switched on Lightroom writes metadata for **every photo in the
catalog** - rewriting existing sidecars, creating new ones next to every
RAW, and writing *into* JPEG/TIFF/PSD/PNG/DNG files themselves. On a large
library (tens of thousands of photos, especially on a network volume) that
runs for hours, makes Lightroom crawl, adds a sidecar beside every raw file,
and may overwrite the app's freshly written ratings and keywords with the
catalog's values before you import them. If you want it, turn it on
deliberately - when Lightroom isn't needed for a while and the app has no
folder waiting to be imported - and expect it to run for a long time; you can
untick it to stop. Otherwise leave it off and use the **Save Metadata to
File** order above for photos you've edited.

Worth spot-checking one file's actual XMP output before trusting a big
batch: `exiftool -G1 -a -s path/to/file.xmp`.

## Customizing the evaluation criteria

The Settings page has an **Evaluation criteria** section with:

- **Shoot context** — the one-line description of what kind of shoot this
  is (defaults to `"a landscape/astrophotography shoot"`). Change it to
  match what you actually shoot, e.g. `"a wedding"` or `"a sports event"`.
- **Reject / Rating / Keyword criteria** — the exact instructions sent to
  Claude for each field. If you change **Shoot context**, revisit these
  too — the defaults assume landscape/astro (e.g. "intentional long
  exposures... are NOT rejects" doesn't make sense for a wedding shoot).

Only the field-name prefix and the JSON response format are fixed in the
prompt, so editing these can tune behavior but can't break the response
parsing.

## Duplicate detection

Checking **Detect duplicates** at triage time groups near-identical
previews (via perceptual hashing — tolerant of exposure differences, so
a bracketed sequence groups together, but sensitive to genuine
compositional differences) before anything is sent to Claude. Only the
sharpest frame per group goes to Claude for full triage; the rest are
marked as rejects with no API call, saving cost on bursts.

**Duplicate threshold** (Settings page, default `5` out of `64`) controls
how similar two previews must be to count as a group — lower is stricter
(fewer, more confident groupings), higher is looser.

The "which frame is sharpest" pick is a local heuristic, not a Claude
judgment — reliable within a same-exposure continuous-shooting burst,
less so across a deliberately bracketed exposure sequence. That's why the
review page shows a thumbnail, a sharpness score, and a one-click
**Use this instead** button for every duplicate group — treat the
auto-pick as a starting guess, not a final answer.

## Optional: mounting a remote photo share

If your photos live on a different machine (e.g. a Mac) than the one
running this app (e.g. a Raspberry Pi), mount that share over SMB rather
than copying files:

**On the Mac:** System Settings → General → Sharing → turn on **File
Sharing** → add the folder → note the Mac's local IP (System Settings →
Wi-Fi/Network → Details).

**On the Pi:**

```bash
sudo apt install cifs-utils -y
sudo mkdir -p /mnt/photos

sudo nano /root/.smbcredentials
```
```
username=your_mac_username
password=your_mac_password
```
```bash
sudo chmod 600 /root/.smbcredentials
sudo nano /etc/fstab
```

Add one line (replace the IP and share name):

```
//192.168.1.XX/Your Share Name /mnt/photos cifs credentials=/root/.smbcredentials,uid=pi,gid=pi,iocharset=utf8,vers=3.02,soft,retrans=1,nofail 0 0
```

A few hard-won specifics: use `vers=3.02`, not `3.0` — some setups don't
mount reliably with `3.0`. Don't add `x-systemd.automount` — it's caused
more mount problems than it's solved in practice. A share name with
spaces needs `\040` escaping in `/etc/fstab` (e.g. `Photos\040and\040
other\040Documents\0401`).

```bash
sudo mount -a
ls /mnt/photos   # should show your folders
```

If your Mac's IP changes (common with some router DHCP setups even with
a "reservation"), the mount will silently go stale — check
`ipconfig getifaddr en0` on the Mac against `/etc/fstab` / `mount` output
on the Pi before assuming anything else is broken.

## Running it permanently (optional, recommended)

A template systemd unit is in [`deploy/photo-triage.service`](deploy/photo-triage.service) — copy it,
fill in the placeholders (working directory, user, API key), and enable it:

```bash
sudo cp deploy/photo-triage.service /etc/systemd/system/photo-triage.service
sudo nano /etc/systemd/system/photo-triage.service   # fill in the placeholders
sudo systemctl daemon-reload
sudo systemctl enable --now photo-triage
```

Check it came up clean:

```bash
systemctl status photo-triage
sudo journalctl -u photo-triage -n 30 --no-pager
```

## Notes

- **Cost**: vision API calls add up over a large backlog — check current
  pricing at anthropic.com and consider testing on one small batch before
  running the full backlog.
- **Usage and cost:** each triage run's API token counts are recorded in
  the folder (`_previews/.usage.json`) and shown on the folder card and the
  review page, with an estimated dollar cost and a running total when a
  folder has been triaged more than once. The dollars are an *estimate*:
  tokens x the per-model list prices in `MODEL_PRICES` in `config.py`
  (update that table when prices change; an unlisted model shows tokens
  only). The Anthropic Console's Usage and Cost pages are the real bill.
- **Disk space:** the extracted preview JPEGs are kept after processing (the
  review thumbnails and re-runs use them) and take roughly 0.5-1 MB per
  photo. Once a folder is fully written, its card offers **Clean up
  previews**, which deletes only those JPEGs - the results, the
  written-photos record, your RAW files and your sidecars stay, and the
  folder still shows as written. New RAW files added later extract as
  normal; **Reprocess folder** re-extracts if you ever need to redo it.
- Generated files (`_previews/`, `triage_results.csv`, `.progress.json`,
  `.completed.json`) live inside each shoot folder under `_previews/` —
  safe to delete a shoot's `_previews/` folder to reset its status back to
  "not started" (the original RAW files and any already-written XMP
  sidecars are untouched). `.completed.json` is the list of photos whose
  sidecar has been written; deleting it makes the folder treat every
  photo as unwritten again.

## License

[MIT](LICENSE) — do whatever you want with it.
