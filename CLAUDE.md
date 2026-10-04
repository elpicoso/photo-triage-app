# Photo Triage App

A Flask app that triages a backlog of RAW landscape/astrophotography files:
extracts JPEG previews, sends them to the Claude API for rate/reject/keyword
suggestions, lets the user review/edit results, then writes ratings and
keywords into XMP sidecars for Lightroom Classic import.

## Infrastructure

- **Repo (edit here):** `/Users/wadecourtney/dev/photo-triage-app` on the Mac
  (username `wadecourtney`). Push to `origin main` on GitHub
  (`elpicoso/photo-triage-app`).
- **Runs on:** Raspberry Pi at `<pi-ip>`, user `wade`. SSH access is
  key-based (no password needed).
- **On the Pi:** working directory `/home/wade/photo-triage-app`, same repo,
  local branch is named `master` but tracks `origin/main` (mismatched names,
  this is expected — don't "fix" it).
- **Service:** systemd unit `photo-triage`, defined at
  `/etc/systemd/system/photo-triage.service`. Runs
  `venv/bin/python3 app.py` as user `wade`, binds to `0.0.0.0:5000`.
- **App URL:** `http://<pi-ip>:5000`
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
3. SSH to the Pi: `ssh wade@<pi-ip>`
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
  `/write`, `/settings`, `/status/<folder>`, `/preview/<folder>/<filename>`.
  Status per folder is derived from `_previews/` contents, the current
  `triage_results.csv`, and a ledger of already-written photos
  (`_previews/.completed.json`, via `read_completed()`/`add_completed()`):
  no previews = "not started"; previews not yet written and not all in the
  CSV = "extracted"; all pending previews in the CSV = "needs review";
  nothing pending = "written". Every RAW extension in the folder is
  handled at once (`metadata.raw_files_by_stem()` maps name stem -> RAW
  file), never a single hardcoded/"most common" one, and a RAW is always
  resolved from the folder by stem, not from a path in a form. Folders
  finished before the ledger existed only have a `.written` marker; for
  those, `read_completed()` falls back to "everything in the last CSV",
  and `migrate_legacy_ledger()` materializes the ledger before the CSV is
  replaced (it must run first, or the new CSV would be read as "done").
  `/extract` and
  `/triage` / `/write` kick off a background thread (`threading.Thread`, requires
  `app.run(..., threaded=True)`) and return immediately rather than
  blocking the request for the full operation; progress is tracked in
  `_previews/.progress.json` (done/total/error), polled by `browse.html`
  via `/status/<folder>` every 1.5s and cleared on success. An error
  leaves the file behind with an `error` key so the UI can show it with a
  "Try again" button - `is_active()` treats that as "not running" so
  retrying is always possible. `/preview/<folder>/<filename>` serves a
  single extracted preview JPEG for the review page's thumbnails.
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
  `triage_folder()` accepts a `duplicate_groups` param (see `dedupe.py`
  below) - non-representative group members are skipped entirely (no API
  call, `reject` defaults to `"yes"`, `duplicate_of` set to the
  representative's filename).
- `dedupe.py` — burst/near-duplicate detection, pure Pillow (no numpy/scipy
  dependency). `compute_dhash()` is a difference hash (compares adjacent
  pixel gradients, not absolute brightness) so it tolerates exposure
  differences across a bracket sequence while still distinguishing a
  genuinely different composition. `group_duplicates()` clusters previews
  via Union-Find over pairwise Hamming distance ≤ `duplicate_threshold`,
  then picks the sharpest member of each group (`sharpness_score()` -
  edge-map variance normalized by the image's own pixel variance, to
  reduce - not eliminate - sensitivity to exposure/contrast differences)
  as the representative sent to Claude. This is a best-effort heuristic,
  not a final judgment - see the review page's swap control below.
- `config.py` — `load_settings()` / `save_settings()`: reads/writes
  `settings.json` (gitignored, lives next to `app.py`, one per machine —
  Mac and Pi each keep their own) with env-var-backed defaults for any
  missing key. This is the runtime config used by the Settings page.
- `templates/browse.html`, `templates/review.html`, `templates/settings.html`,
  `templates/help.html` — UI. `review.html` shows a thumbnail per row
  (via `/preview/...`) and, for rows in a duplicate group, a sharpness
  score and a "Use this instead" button that reassigns which group member
  is "kept" (client-side only - just flips the Reject dropdowns for every
  row sharing that group, same as manually editing them).

## Settings page

`/settings` lets you edit `raw_root`, `raw_extensions`, `triage_model`,
`batch_size`, `max_preview_dimension`, `shoot_context`, the three
evaluation criteria (`reject_criteria`, `rating_criteria`,
`keyword_criteria`), and `duplicate_threshold` from the browser — writes
to `settings.json`, takes effect on the next request, no restart needed.
`ANTHROPIC_API_KEY` is deliberately NOT here (see Infrastructure above) —
it stays secret-only, in the systemd unit file.

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
- `/write` used to run inside the HTTP request and hit a 500 whenever a
  folder took long enough that the page looked hung and got re-submitted:
  writing is ~5s/photo over SMB (a 144-RAW folder is ~12 min), and the
  overlapping requests raced `write_sidecar`'s `exists()` check against
  `exiftool -o`, which refuses to overwrite ("already exists") - an
  uncaught `CalledProcessError`. Fixed two ways: `/write` now runs in the
  background with a progress bar and ignores a second submit while one is
  active (`is_active()`), and `write_sidecar` treats exiftool's own
  "already exists" message as success (deliberately not re-checking
  `exists()`, since a stale SMB lookup cache could lie twice). Don't
  restart the service while a write is in progress - the thread dies
  mid-folder (harmless to redo, but wasteful).
- Folders can mix camera bodies (the Italy trip had 144 `.CR3` + 46 `.CR2`
  in one directory). The app used to pick the single most common RAW
  extension per folder and silently skip the rest - no previews, no
  triage, no sidecars, yet the folder showed "written" while Lightroom
  counted 192 files. Now every RAW type is handled, and re-running a
  finished folder is incremental on purpose: extract only does files
  missing a preview, triage/dedupe/review/write only touch photos not in
  `.completed.json`. That ledger is what stops a re-run from re-writing
  sidecars with stale values - including anything changed in Lightroom
  since. Two RAWs in one folder sharing a name stem across extensions
  would collide on `<stem>.jpg` and `<stem>.xmp` (Lightroom can't tell them
  apart either); the more common extension wins and the other is skipped
  with a warning in the journal. AppleDouble `._*` stubs from the SMB
  share are ignored.
- `dedupe.sharpness_score()`'s representative pick is a best-effort proxy,
  not a reliable judgment — verified during testing that it correctly
  ranks sharp > blurry within a same-exposure burst, but a synthetic
  wide-exposure-bracket test showed the metric can be fooled by
  contrast/brightness differences (a brighter frame can score "sharper"
  at identical focus). This is a known, accepted limitation (confirmed
  with Wade - he culls astro manually and doesn't burst-shoot it, so the
  main risk case doesn't come up in practice) rather than something to
  "fix" - it's exactly why the review page shows thumbnails, a sharpness
  score, and a one-click "Use this instead" swap instead of trusting the
  auto-pick silently.

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
