# Ring Density Monitor

A small companion app for Elite Dangerous exploration: watches your live
journal, and the moment an FSS/full body scan reveals a planetary or
stellar ring, shows its surface density compared to the galactic norm for
its type (Icy / Metallic / Metal Rich / Rocky) — color-coded so a glance
tells you whether it's worth a DSS pass.

```
Body   Ring   Type         Density      Δ Galactic
3      A      Rocky        12.2 t/km²   +35%
3      B      Icy          12.6 t/km²   +47%
```

- **Black** (< +90%): not worth your time.
- **Green** (>= +90%, < +99%): should DSS.
- **Red** (>= +99%): elite ring, must DSS.

Black-tier rows auto-hide 3 minutes after being scanned to keep the view
uncluttered; green/red rows stay until you jump to a new system.

## Installation (end users)

1. Download the latest installer (`RingDensityMonitor-Setup-X.Y.Z.exe`) from
   this repo's [Releases](../../releases) page and run it. It installs
   per-user (no admin rights needed) and adds a Desktop/Start Menu shortcut.
   Since the installer and app aren't code-signed, Windows SmartScreen (and
   possibly your antivirus) will treat it with suspicion the first time —
   see [Antivirus and SmartScreen warnings](#antivirus-and-smartscreen-warnings)
   below before you download, so you know what to expect and how to verify
   the file.
2. Launch **Ring Density Monitor** from the Desktop shortcut. It finds your
   journal automatically at the standard
   `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\` location
   — no setup needed. If your journal lives somewhere non-standard, see
   [Configuration](#configuration) below.
3. Play. Rings appear in the table as your FSS/scans reveal them.

Building from source instead (for development, or to build your own
installer) is covered in [Dependencies](#dependencies) and
[Building a release](#building-a-release) below.

## Antivirus and SmartScreen warnings — what to expect and what to do

Ring Density Monitor is a small open-source tool. Its releases are currently
**not code-signed** (code-signing certificates are expensive and reputation
takes time to build), so Windows and some antivirus products will treat the
installer with suspicion the first time they see it. **This is expected and
does not mean anything is wrong with the file** — it means Windows has never
seen this exact file before and no publisher identity is attached to it.
You can (and should) verify the download yourself; instructions below.

### First: verify your download

Every release on the [Releases](../../releases) page lists a SHA-256
checksum for the installer. After downloading, open PowerShell and run:

    Get-FileHash .\RingDensityMonitor-Setup-X.Y.Z.exe -Algorithm SHA256

If the hash printed matches the one in the release notes, your file is
byte-for-byte the one the maintainer published, and the warnings below are
safe to click through. If it does **not** match, delete the file and
re-download from the Releases page only — never from a third-party mirror.

### "Windows protected your PC" (SmartScreen) — blue dialog

This appears because the installer is new and unsigned, not because
anything harmful was detected. If your checksum matched:

1. Click **More info**.
2. Click **Run anyway**.

That's it — SmartScreen only gates the first run.

### Your antivirus flags, quarantines, or deletes the file

Some antivirus products go further than a warning and quarantine the
installer or the installed `RingDensityMonitor.exe`. This is a **false
positive** with a known cause: the app is packaged with PyInstaller (a
standard tool that bundles a Python program and the Python runtime into an
exe), and because some actual malware is also built with PyInstaller, a few
antivirus engines flag *everything* built with it. See PyInstaller's own
[note on antivirus false positives](https://github.com/pyinstaller/pyinstaller/blob/develop/.github/ISSUE_TEMPLATE/antivirus.md).

If this happens:

1. **Verify the checksum first** (above). Only proceed if it matches.
2. **Restore the file from quarantine** using your AV's quarantine/history
   screen (the wording varies: "Restore", "Allow", "Not a threat").
3. **Add an exclusion** so it doesn't recur — either for the installer
   file, or (after installing) for the app folder:
   `%LOCALAPPDATA%\Programs\RingDensityMonitor`
   In Windows Security this is under: Virus & threat protection →
   Manage settings → Exclusions → Add or remove exclusions.
4. Optionally, **report the false positive** to your AV vendor — this
   genuinely helps: enough reports get the file whitelisted for everyone.

**Never add exclusions for files whose checksum you haven't verified.**
The verify-then-restore order matters: the checksum is what tells you the
flagged file really is the one published here.

### Why not just sign the releases?

Code signing is a possible future improvement (e.g. via
[SignPath Foundation](https://signpath.org/), which offers free signing for
qualifying open-source projects) but isn't in place yet. Until then,
checksums + this documentation are the interim answer. If a release is ever
signed, this section will be updated.

## Configuration

The app needs no configuration for the vast majority of installs — it finds
the journal at the standard Saved Games location automatically. If yours
lives somewhere else, override it either:

- Per-launch: `RingDensityMonitor.exe --journal-dir "D:\path\to\journal"`
  (or `python -m app.main --journal-dir ...` running from source), or
- Persistently: create/edit
  `%APPDATA%\RingDensityMonitor\config.json`:
  ```json
  { "journal_dir": "D:\\path\\to\\journal" }
  ```

Precedence: `--journal-dir` flag > `config.json` > built-in default.

## How rings are scored

See [`docs/ring-ranker.md`](docs/ring-ranker.md) for the surface-density
methodology and [`docs/galactic-sigma-display-spec.md`](docs/galactic-sigma-display-spec.md)
for the galactic-sigma display convention this app implements. In short:
surface density = ring mass / ring area, compared against the galaxy-wide
median density for that ring type
([`data/baselines/galactic_ring_baselines.json`](data/baselines/galactic_ring_baselines.json)),
expressed as a percentage delta.

## Dependencies

Runtime: none beyond the Python standard library (tkinter included). See
[`requirements.txt`](requirements.txt).

Build-time (only if building from source): PyInstaller. See
[`requirements-dev.txt`](requirements-dev.txt).

Running from source:

```bash
python -m app.main
```

## Building a release

Packaging is manual (no CI currently) — from a Windows machine with
[Inno Setup](https://jrsoftware.org/isinfo.php) installed:

```bash
pip install -r requirements-build.lock
pyinstaller RingDensityMonitor.spec --clean
iscc installer.iss
```

`requirements-build.lock` pins the exact dependency versions an official
release was built with (unlike `requirements.txt`/`requirements-dev.txt`,
which use minimum-version ranges for general development). Record the Python
version used in the release notes alongside it. Optionally set
`SOURCE_DATE_EPOCH` (to the release's Unix timestamp) before building for
closer build-output reproducibility — PyInstaller otherwise embeds the
current time.

This produces `dist/RingDensityMonitor/` (the onedir PyInstaller build) and
then `dist-installer/RingDensityMonitor-Setup-X.Y.Z.exe` (the installer, via
`installer.iss`). Bump `MyAppVersion` in `installer.iss` **and** `filevers`/
`prodvers`/`FileVersion`/`ProductVersion` in `version_info.txt` together
before building a new release. Neither `dist/` nor `dist-installer/` are
committed to git (see `.gitignore`) — attach the built installer `.exe` to a
new GitHub Release instead, along with its SHA-256 checksum
(`Get-FileHash .\RingDensityMonitor-Setup-X.Y.Z.exe -Algorithm SHA256`); it's
a compiled binary and doesn't belong in git history.

Before tagging a release: install and run the built installer on a machine
without a Python/dev environment, confirm the app launches and detects the
journal correctly, and confirm uninstall removes the app cleanly. Consider
scanning the built installer on [VirusTotal](https://www.virustotal.com/)
before announcing, so you know the actual per-vendor detection picture
rather than guessing.

## Repository layout

```
core/
  journal_watcher.py     Standalone live journal tailer (no cross-project deps)
  ring_display.py        Adapter: raw Scan events -> display rows (density, delta, tier)
  ring_analysis.py        Surface density + sigma-normalization scoring
  ring_baseline_library.py  Galactic/sector ring baseline loading

app/
  main.py           Entry point (`python -m app.main`, and the PyInstaller build target)
  ring_table.py     Tkinter widget: live ring table with per-cell delta coloring
  config.py         Persisted settings (%APPDATA%\RingDensityMonitor\config.json)

RingDensityMonitor.spec   PyInstaller build spec (onedir, custom icon)
installer.iss             Inno Setup script that wraps the PyInstaller build into an installer
icon.ico / icon.png        App icon (.ico is what's actually embedded in the build)
requirements.txt           Runtime dependencies (none)
requirements-dev.txt        Adds build-time dependencies (PyInstaller) on top of requirements.txt

data/baselines/    Galactic and sector ring density baselines
docs/              Ring scoring methodology notes
```

## Legal

MIT License — see [`LICENSE`](LICENSE). Not affiliated with Frontier
Developments. Elite Dangerous is a trademark of Frontier Developments plc.
