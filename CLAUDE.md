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
- **Env vars for the service** (`ANTHROPIC_API_KEY`, `RAW_ROOT`) are set via
  `Environment=` lines directly in the systemd unit file — NOT a `.env`
  file. When editing that file, each line needs the full `NAME=value`
  form; a bare value with no `NAME=` prefix silently does nothing (this
  has happened before and caused every Claude API call to fail with an
  auth error, with no obvious symptom other than "ERROR: ..." appearing
  in the keywords column).

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
  `/write`. Status per folder tracked by presence of `_previews/`,
  `_previews/triage_results.csv`, and `_previews/.written`.
- `metadata.py` — `extract_previews()` (calls exiftool to pull embedded
  JPEG previews from RAW files) and `write_sidecar()` (writes rating/
  keywords into XMP sidecars via exiftool, using fully-qualified tag names:
  `-XMP-xmp:Rating`, `-XMP-dc:Subject`, `-IPTC:Keywords` — NOT the generic
  `-Rating`/`-Keywords` shortcuts, which can land in the wrong namespace
  like `pdf:Keywords` on a freshly created sidecar).
- `triage.py` — calls the Claude API (Haiku model, see `config.py`) in
  batches to get reject/rating/keyword suggestions per image.
- `templates/browse.html`, `templates/review.html` — UI. Note: there are
  stray duplicate copies of these two files sitting in the repo ROOT
  (not `templates/`) left over from an earlier mistake — harmless (Flask
  doesn't read them from there) but should be deleted for clarity.

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
- `raw_ext` is currently hardcoded per-template rather than remembered
  from the extract step — if a folder has `.CR3` files but a form still
  defaults to `"CR2"` (or vice versa), the `/write` route will silently
  skip every row (no error, no sidecar) because `raw_path` never matches
  an actual file on disk. Worth fixing properly (see backlog).
- Never paste a live API key or other secret directly into a chat session
  — if it happens, treat it as compromised and rotate it immediately via
  the Anthropic Console (API Keys → disable old key → Create Key).

## Backlog

- **Bug:** `raw_ext` should be threaded through from the extract step
  automatically instead of being hardcoded (currently `"CR2"` in some
  places) — currently causes silent write failures on `.CR3` folders.
- **Feature:** progress indicator in the UI showing extraction/processing
  progress on files (currently only visible via `journalctl -f` or
  watching the `_previews` folder fill up).
- **Feature:** selectable operations per extraction job (rate / keyword /
  evaluate) instead of always running all three — implemented at the
  triage step (via checkboxes + `.ops.json`) and overridable again at
  write time. Already shipped as of the last session; verify it's
  working end-to-end (a folder run with keywords unchecked should come
  back with an empty keywords column, and unchecking at write time
  should produce a sidecar with no `XMP-dc:Subject`/`IPTC:Keywords` tags).
- **Feature:** duplicate detection — likely perceptual hashing rather
  than exact-file hashing, since Wade shoots bursts; more Pi compute per
  file, needs its own review UI for flagged duplicates. Not yet designed.
