# Changelog

## 0.2.2 (2026-10-14)

### Updates (OTA)
- The installed app checks GitHub Releases at startup and every 24 h. You can turn the check off, and there are stable and beta channels. It uses ETag caching and respects rate limits.
- When a newer version exists, the tray and the web UI show **Update available**. **Update now** downloads the installer, checks it against the release's `SHA256SUMS`, installs it silently and restarts OrcPixels. Your settings, displays, playlists and Library stay as they are.
- Source checkouts only show a `git pull` notice. See [docs/UPDATES.md](docs/UPDATES.md).
- 0.2.1 has no updater, so install 0.2.2 by hand once. Updates from 0.2.2 onward install through the app.

### Animated GIF import
- Each frame is decoded with its own timing, and the source shows the frame count, length and loop count. You can trim, change the speed (0.25x to 4x), fps and max frames, and pick a layout (adaptive / contain / crop).
- Seamless-loop fixes (ping-pong, crossfade).
- "Fit to the sign's limits" keeps BLE uploads within the 15 KB / 64-frame budget.
- "Save as preset" also keeps the original GIF in the Library under `imports/`. "+ playlist" adds it to the playlist editor.

### Adaptive layouts
- Scenes lay out for the panel they run on: aspect ratio sets the composition, pixel size sets the detail, and hardware topology sets the LED mapping.
- Five adaptive scenes, with Native / Adaptive / Scaled / Fallback compatibility ratings and a "Try the resolution" preview in the UI.
- Golden references keep the 64x16 and 64x64 output unchanged. See [docs/DESIGN.md](docs/DESIGN.md).

### Live sources by category
- Live sources now come from a plugin registry, grouped by category. The Live tab is collapsible and searchable, with a switch for each screen.
- New **Sports** screens (ESPN: 9 leagues, Live now, My teams).
- New **Entertainment** screens (TVMaze, iTunes charts, Wikipedia trending).
- All of them are adaptive at any panel size. See [docs/LIVE_SOURCES.md](docs/LIVE_SOURCES.md).

### Release
- The release workflow publishes `SHA256SUMS` next to the installer.

## 0.2.1 (2026-10-09)
- First Windows installer release: tray app, Library folder, per-user data in `%APPDATA%\OrcPixels`, opt-in LAN access.
- Fixes timing races in the test suite (0.2.0 was never released) and fixes the Add display form ignoring a profile's size.
