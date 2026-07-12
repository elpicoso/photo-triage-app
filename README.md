# Photo Triage — Pi Setup

## 1. Share the drive from your Mac

System Settings → General → Sharing → turn on **File Sharing** → add the
"Photos and other Documents 1" volume → note your Mac's local IP
(System Settings → Wi-Fi/Network → Details).

## 2. Mount it on the Pi

```bash
sudo apt update
sudo apt install cifs-utils exiftool python3-venv -y

sudo mkdir -p /mnt/photos

# Credentials file, not inline in fstab
sudo nano /root/.smbcredentials
```
Contents:
```
username=your_mac_username
password=your_mac_password
```
```bash
sudo chmod 600 /root/.smbcredentials
sudo nano /etc/fstab
```
Add one line (replace the IP and share name with your Mac's):
```
//192.168.1.XX/Photos and other Documents 1 /mnt/photos cifs credentials=/root/.smbcredentials,uid=pi,gid=pi,iocharset=utf8,x-systemd.automount,nofail,vers=3.0 0 0
```
```bash
sudo mount -a
ls /mnt/photos   # should show your shoot folders
```
If it fails to mount, the most common fix is dropping the SMB version —
try `vers=2.1` or `vers=1.0` in place of `vers=3.0`.

## 3. Install the app

```bash
cd ~
git clone <wherever you put this> photo-triage-app   # or copy the files over
cd photo-triage-app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Set your API key

```bash
echo 'export ANTHROPIC_API_KEY="your-key-here"' >> ~/.bashrc
source ~/.bashrc
```
Get a key from console.anthropic.com if you don't have one — this is billed
separately from any Claude.ai subscription, pay-as-you-go per API call.

## 5. Run it

```bash
source venv/bin/activate
python3 app.py
```
Visit `http://<pi-ip>:5000` from any browser on your network.

## 6. Auto-start on boot (optional, recommended)

```bash
sudo nano /etc/systemd/system/photo-triage.service
```
```ini
[Unit]
Description=Photo Triage App
After=network-online.target mnt-photos.mount
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/photo-triage-app
Environment="ANTHROPIC_API_KEY=your-key-here"
ExecStart=/home/pi/photo-triage-app/venv/bin/python3 app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable photo-triage
sudo systemctl start photo-triage
```
Now it's always running at `http://<pi-ip>:5000` — bookmark that or add it
as a tile to whatever home dashboard you're using.

## Using it

1. Pick a folder from the list, confirm the RAW extension, click
   **Extract previews** — runs exiftool, same as the manual version.
2. Click **Run triage** — sends batches of preview JPEGs to the Claude API
   (Haiku by default, cheap enough to run on the full backlog) and writes
   `triage_results.csv`.
3. Click **Review results** — this is the same manual check you were doing
   before, just as an editable web table instead of opening the CSV
   directly. Fix anything before submitting.
4. **Write to XMP** — writes the sidecars, same logic we debugged earlier
   (fully-qualified tag names, so no repeat of the `pdf:Keywords` issue).
5. On your Mac, Lightroom: select the folder → **Metadata → Read Metadata
   from File**. This step stays manual — Lightroom doesn't run on the Pi.

## Notes

- **Cost**: Haiku vision calls are inexpensive, but 5,000+ images will add
  up — check pricing at anthropic.com before running the full backlog, and
  consider testing cost on one batch first.
- **First run on the real backlog**: still worth spot-checking one file's
  XMP output with `exiftool -G1 -a -s` before trusting a big batch, same as
  we did manually.
- **The `raw_ext` field** on each action defaults to `CR2` — change it per
  folder if a shoot used a different camera/extension.
