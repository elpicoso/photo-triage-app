# Photo Triage

A small self-hosted Flask app that triages a backlog of RAW files: it pulls
the embedded JPEG preview out of each RAW file, sends batches of previews to
the Claude API for reject/rating/keyword suggestions, lets you review and
edit the results in a browser, then writes your (possibly edited) choices
into XMP sidecar files that Lightroom Classic (or any XMP-aware catalog
tool) can pick up.

Originally built for landscape/astrophotography, but the evaluation
criteria are fully editable from the Settings page — see
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
| Shoot context, reject/rating/keyword criteria | — | landscape/astro-tuned defaults |

`ANTHROPIC_API_KEY` is deliberately **not** on the Settings page — it's a
secret, and the Settings page is reachable by anyone who can reach the
app on your network. Set it as an environment variable only (see
[Running it permanently](#running-it-permanently-optional) below for the
systemd version).

The RAW extension for a given folder doesn't need to be set anywhere —
it's auto-detected from whatever's actually in the folder.

## Using it

1. Browse to a folder that directly contains RAW files — the app shows a
   card with a status and an action button.
2. **Extract previews** — pulls the embedded JPEG preview out of each RAW
   file via `exiftool`. Runs in the background with a live progress bar;
   no need to watch logs or refresh.
3. **Run triage** — choose which of Flag rejects / Rate / Keyword you want
   for this folder, then sends batches of preview JPEGs to the Claude API.
   Also runs in the background with a progress bar. If a batch fails
   (network hiccup, rate limit), the folder's progress panel shows the
   error with a **Try again** button instead of hanging.
4. **Review results** — an editable table of every file's suggested
   reject/rating/keywords. Fix anything before writing — this is the
   checkpoint that catches anything Claude got wrong.
5. **Write to XMP** — writes your (possibly edited) choices into XMP
   sidecar files next to each RAW, using fully-qualified tag names
   (`XMP-xmp:Rating`, `XMP-dc:Subject`) so they land where Lightroom
   actually looks for them. You can uncheck Rating or Keywords here to
   skip writing that field for this batch — a rejected row's `Rating=-1`
   is always written regardless, since reject is meant to be a permanent
   judgment, not something to selectively skip.
6. In Lightroom: select the folder, then **Metadata → Read Metadata from
   File**. This step is manual — "Synchronize Folder" alone won't pick up
   changed sidecars on photos already in the catalog, only added/removed
   files.

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
- Generated files (`_previews/`, `triage_results.csv`, `.progress.json`,
  `.written`) live inside each shoot folder under `_previews/` — safe to
  delete a shoot's `_previews/` folder to reset its status back to "not
  started" (the original RAW files and any already-written XMP sidecars
  are untouched).
