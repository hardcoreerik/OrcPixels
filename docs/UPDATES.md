# Updates (OTA)

OrcPixels checks the public [Releases](https://github.com/hardcoreerik/OrcPixels/releases) page for new versions and installs one only when you ask.

## Checking

- The check runs at startup (after 20 s) and then every 24 h. Turn it off under **Settings → Updates**. **Check for updates** checks right away, at most once a minute.
- The check calls `GET https://api.github.com/repos/hardcoreerik/OrcPixels/releases` anonymously. It sends the last `ETag` (an unchanged list returns `304`, which doesn't use up GitHub's 60 requests per hour) and backs off until `X-RateLimit-Reset` if the limit is hit. The result is cached in `%APPDATA%\OrcPixels\out\update.json`, and the last good answer is kept when GitHub can't be reached.
- **Channel:** `stable` (default) offers only full releases. `beta` also offers prereleases (tags like `v0.3.0-beta.1`). Versions are compared as semver.
- When a newer version exists, **Update available vX** appears in the tray menu and in the web UI's side panel, with the release notes and a link.

## Installing (installed Windows build only)

**Update now**, in the side panel or under Settings → Updates (the tray item opens it):

1. Downloads `OrcPixels-Setup-<version>.exe` and the release's `SHA256SUMS` to a temp folder.
2. Checks the installer's SHA-256 against `SHA256SUMS`. If anything is missing or doesn't match, the file is deleted and nothing is installed.
3. Starts the installer with `/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /RELAUNCH=1`, into the same folder (`/DIR`) with the same data and Library folders (`/CONFIGDIR`, `/LIBRARYDIR`). A Program Files install runs through Windows' "run as administrator" prompt (UAC); a per-user install (`/CURRENTUSER`) needs no prompt.
4. Stops the server cleanly: Live engines stop, and BLE links close through the normal shutdown. Nothing is sent to a sign.
5. When the install finishes, the installer starts OrcPixels again as the normal user.

Your settings, displays, playlists, user presets and Library live in `%APPDATA%\OrcPixels` and the Library folder. The installer never writes there, except `config\install.json` (Library path and installed version).

Updates never install silently on their own.

## Source checkouts / dev mode

A checkout (`python -m orcpixels serve`, `run.bat`) never updates itself. It shows a notice: *"vX is out. Update with `git pull`"*.

## Rolling back

Every earlier installer stays on the [Releases](https://github.com/hardcoreerik/OrcPixels/releases) page. To go back, download the version you want and run it. It installs over the current version and keeps your data.

## Code signing

The installers are **not code-signed** yet. Windows SmartScreen may say "Windows protected your PC" the first time; choose *More info → Run anyway*. The updater's integrity check is the SHA-256 published with each release, which is produced by the release workflow (`.github/workflows/release.yml`) from the built file. That protects against corrupted or swapped downloads, but it doesn't verify the publisher the way Authenticode does. Signing can be added to the workflow (for example `signtool` with an OV/EV certificate, or Azure Trusted Signing) without changing the updater.

## Release checklist

The tag `vX.Y.Z` (or `vX.Y.Z-beta.N` for a prerelease) builds the installer, writes `SHA256SUMS` and attaches both to the release.

## Testing

- `tests/test_updater.py` mocks the GitHub API and the downloads.
- `tools/update_mock.py DIR` serves local installers as a fake Releases API. Point an installed build at it with `ORCPIXELS_UPDATE_API=http://127.0.0.1:8799/releases`.
