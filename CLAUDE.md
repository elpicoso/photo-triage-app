# Photo Triage App

A Flask app that triages a backlog of RAW landscape/astrophotography files:
extracts JPEG previews, sends them to the Claude API for rate/reject/keyword
suggestions, lets the user review/edit results, then writes ratings and
keywords into XMP sidecars for Lightroom Classic import.

## Infrastructure

- **Repo (edit here):** `/Users/wadecourtney/dev/photo-triage-app` on the Mac
  (username `wadecourtney`). Push to `origin main` on GitHub
  (`elpicoso/photo-triage-app`).
- **Runs on:** Raspberry Pi at `192.168.1.170`, user `wade`. SSH access is
  key-based (no password needed).
- **On the Pi:** working directory `/home/wade/photo-triage-app`, same repo,
  local branch is named `master` but tracks `origin/main` (mismatched names,
  this is expected — don't "fix" it).
- **Service:** systemd unit `photo-triage`, defined at
  `/etc/systemd/system/photo-triage.service`. Runs
  `venv/bin/python3 app.py` as user `wade`, binds to `0.0.0.0:5000`.
- **App URL:** `http://192.168.1.170:5000`
- **Photos:** live on Wade's Mac (`Photos and other Documents 1` volume),
  SMB-mounted on the Pi at `/mnt/photos`. The Mac's LAN IP can drift even
  with a DHCP reservation supposedly in place (has happened before,
  cause not fully resolved) — if the app throws "Host is down" or the
  mount looks dead, check the Mac's actual current IP
  (`ipconfig getifaddr en0`) against what's in the Pi's `/etc/fstab` /
  active `mount` output before assuming anything else is broken.
- **`ANTHROPIC_API_KEY`** is set via an `Environment=` line directly in the
  systemd unit file — NOT a `.env` file, and NOT editable from the app (see
  Settings page below; deliberately excluded since the app is reachable
  from anywhere on the LAN). When editing that file, each line needs the
  full `NAME=value` form; a bare value with no `NAME=` prefix silently does
  nothing (this has happened before and caused every Claude API call to
  fail with an auth error, with no obvious symptom other than "ERROR: ..."
  appearing in the keywords column).
- **`RAW_ROOT`, triage model, batch size, max preview dimension, RAW
  extensions** — these still have env var fallbacks (`RAW_ROOT`,
  `TRIAGE_MODEL`, `BATCH_SIZE`, `MAX_PREVIEW_DIMENSION`) read at import
  time in `config.py`, but as of the Settings page (below) they're
  normally overridden at runtime via `settings.json` instead, no restart
  needed.

## Deploy workflow

1. Edit files in the Mac repo.
2. `git add`, `git commit`, `git push` (pushes to `origin main`).
3. SSH to the Pi: `ssh wade@192.168.1.170`
4. `cd /home/wade/photo-triage-app && git pull`
5. `sudo systemctl restart photo-triage`
6. `systemctl status photo-triage` — confirm the "Active since" timestamp is
   fresh before assuming the deploy worked.
7. Sanity check with `sudo journalctl -u photo-triage -n 30 --no-pager` if
   anything seems off.

There used to be a `~/update-triage.sh` one-command script for steps 3-6,
but it no longer exists on the Pi (unclear if it was ever actually created,
or got removed) — do the manual steps above until/unless it's recreated.

## Code layout

- `app.py` — Flask routes: `/browse`, `/extract`, `/triage`, `/review`,
  `/write`, `/settings`, `/status/<folder>`. Status per folder tracked by
  presence of `_previews/`, `_previews/triage_results.csv`, and
  `_previews/.written`. RAW extension per folder is auto-detected from
  what's actually on disk (`detect_raw_ext()`), not hardcoded or
  form-supplied. `/extract` and `/triage` kick off a background thread
  (`threading.Thread`, requires `app.run(..., threaded=True)`) and return
  immediately rather than blocking the request for the full operation;
  progress is tracked in `_previews/.progress.json` (done/total/error),
  polled by `browse.html` via `/status/<folder>` every 1.5s and cleared on
  success. An error leaves the file behind with an `error` key so the UI
  can show it with a "Try again" button - `is_active()` treats that as
  "not running" so retrying is always possible.
- `metadata.py` — `extract_previews()` (calls exiftool to pull embedded
  JPEG previews from RAW files) and `write_sidecar()` (writes rating/
  keywords into XMP sidecars via exiftool, using fully-qualified tag names:
  `-XMP-xmp:Rating`, `-XMP-dc:Subject` — NOT the generic `-Rating`/
  `-Keywords` shortcuts, which can land in the wrong namespace like
  `pdf:Keywords` on a freshly created sidecar. No `-IPTC:Keywords` tag —
  IPTC-IIM has no home in a bare `.xmp` file, so exiftool silently no-ops
  that write; Lightroom's Keywords panel reads `XMP-dc:Subject` anyway).
- `triage.py` — calls the Claude API (model/batch size/preview size from
  `config.load_settings()`) in batches to get reject/rating/keyword
  suggestions per image. Preview JPEGs are downscaled (Pillow) to
  `max_preview_dimension` before being base64-encoded and sent.
- `config.py` — `load_settings()` / `save_settings()`: reads/writes
  `settings.json` (gitignored, lives next to `app.py`, one per machine —
  Mac and Pi each keep their own) with env-var-backed defaults for any
  missing key. This is the runtime config used by the Settings page.
- `templates/browse.html`, `templates/review.html`, `templates/settings.html`,
  `templates/help.html` — UI.

## Settings page

`/settings` lets you edit `raw_root`, `raw_extensions`, `triage_model`,
`batch_size`, `max_preview_dimension`, `shoot_context`, and the three
evaluation criteria (`reject_criteria`, `rating_criteria`,
`keyword_criteria`) from the browser — writes to `settings.json`, takes
effect on the next request, no restart needed. `ANTHROPIC_API_KEY` is
deliberately NOT here (see Infrastructure above) — it stays secret-only,
in the systemd unit file.

`shoot_context` is the one-line genre framing ("a landscape/astrophotography
shoot" by default) in the triage prompt — this repo started as
Wade-specific but is meant to be installable by others for any genre; see
`README.md` (general install docs, not Wade's Pi-specific setup — that
stays here) and `deploy/photo-triage.service` / `Dockerfile` /
`docker-compose.yml` for the two supported install paths.

The evaluation criteria are the exact instruction text sent to Claude
for the "reject:", "rating:", and "keywords:" lines in the triage
prompt (see `triage.build_prompt()`) — only the field-name prefix and
the JSON response format are fixed, so editing these can't break the
response shape, just tune what counts as a reject, how generous ratings
are, or keyword style/count. All three are required (non-empty) since
an empty criteria line would leave Claude with no instruction for a
field it's still being asked to fill in.

## Known gotchas / hard-won fixes

- SMB mount options matter: `vers=3.02` (not `3.0`), fstab paths with
  spaces need `\040` escaping, and do NOT use `x-systemd.automount`.
- The mount is currently `soft,retrans=1` — meaning it fails fast rather
  than retrying on transient network hiccups. Has caused
  "0 output files created" from exiftool right after a mount recovers,
  even though the files are actually fine. If this recurs, consider
  bumping `retrans` or switching to a `hard` mount with a sane timeout.
- macOS SMB auth issues: toggling the Windows File Sharing checkbox in
  System Settings → Sharing → File Sharing → Options forces credential
  hash regeneration and can fix stuck auth.
- The extract route (`extract_previews()` in `metadata.py`) already does
  `os.makedirs(preview_dir, exist_ok=True)` — the previews directory does
  NOT need to be pre-created; if a "no such file" error shows up, look
  elsewhere first.
- Camera-embedded RAW previews vary wildly in size — some bodies embed
  full-resolution JPEGs (5-10MB+ each). Triage sends `batch_size` previews
  per API call; unresized, that can exceed the API's request size limit
  (413 `request_too_large`) once base64-encoded (~33% bigger than raw).
  Fixed by downscaling to `max_preview_dimension` (config/settings, default
  1568px long edge — Claude's own recommended max) before encoding, but if
  this recurs after someone lowers that setting or a camera embeds
  something even larger, that's where to look.
- Never paste a live API key or other secret directly into a chat session
  — if it happens, treat it as compromised and rotate it immediately via
  the Anthropic Console (API Keys → disable old key → Create Key).
- `/write`'s reject flag has no write-time override (unlike rating and
  keywords, which have checkboxes in `review.html`) — any row with
  `reject=yes` always gets `XMP-xmp:Rating=-1` + `Label=Red` written.
  This is intentional (confirmed with Wade): reject is a binary, permanent
  judgment, not something worth selectively skipping at write time the
  way you might skip keywords for speed.

## Lightroom import gotcha

After running `/write` and confirming XMP sidecars have the expected data
(e.g. `exiftool -XMP-dc:Subject file.xmp`), **"Synchronize
Folder" in Lightroom is NOT enough** to pull in the new metadata for photos
already in the catalog — it only detects added/removed files, not changed
sidecars. To actually load the new rating/keywords:

- Select the affected photos in the Library grid
- Right-click → **Metadata → Read Metadata from File**
- Confirm the overwrite warning

If keywords/ratings look correct in the XMP file via exiftool but don't
show up in Lightroom after a sync, this is almost always the cause — check
here before assuming the write step failed.

## Backlog

- **Feature:** duplicate detection — likely perceptual hashing rather
  than exact-file hashing, since Wade shoots bursts; more Pi compute per
  file, needs its own review UI for flagged duplicates. Not yet designed.
