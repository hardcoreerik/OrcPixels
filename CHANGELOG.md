# Changelog

## 0.2.5 (2026-10-15)

### Fixed: "Update now" rolled back
- The update installer no longer races the app's shutdown: OrcPixels passes its process id (`/WAITPID`) and Setup waits for it to exit before checking for files in use. Setup also waits by name when an older OrcPixels (0.2.2 to 0.2.4) starts it.
- A browser opened from OrcPixels no longer inherits OrcPixels' DLL folder, so browser helpers stop loading `VCRUNTIME140.dll` from the install folder and blocking updates.
- Setup only treats OrcPixels' own programs as "in use". The shared Microsoft runtime DLLs are version-checked: an unchanged copy is skipped, and a newer one that's locked is replaced at the next restart.
- When an update fails, Setup starts the old version again, and the Updates card shows why, taken from the installer log. The previous attempt's log is kept as `update-install.prev.log`.

## 0.2.4 (2026-10-15)

### Free API live sources
- 31 new Live screens from free, keyless APIs (picked from the public-apis list), already sorted into categories and working with no setup:
  - **Weather:** world weather now, next 12 h, 7 days and UV (Open-Meteo, works anywhere).
  - **Space:** next launch countdown and upcoming launches (Launch Library 2), space news, people in space, asteroids this week (NASA), natural events near you (NASA EONET).
  - **Markets:** crypto Fear & Greed, Bitcoin fees and block height, US national debt.
  - **Ham Radio:** POTA spots, nearest POTA activators, POTA activity by band.
  - **Sports:** F1 next race, driver standings and last result.
  - **Time & Local:** next public holidays, bike share near home.
  - **Apps:** a service status board (GitHub, Cloudflare, Discord, OpenAI, Reddit, Claude).
  - **Games** (new category): new free-to-play games, Steam deals.
  - **Fun & Facts** (new category): dad joke, fun fact, quote of the day, advice, trivia, latest xkcd, on this day.
- Location sources use your home location, or the FT8 home grid. Units and holidays follow your country.
- **Settings > Free sources > Get more free sources** downloads the latest source catalog, which is plain data checked before use, and adds new screens without an app update.
- Services that take a key get a **Get free key** button and one paste field. NASA is the only one, and it is optional: NASA screens use the public demo key until you add your own.

## 0.2.3 (2026-10-14)

### RF / Signal presets
- New preset family **RF / Signal**: 10 animated concepts in a Midnight Synth palette (blue, violet and magenta on black), drawn for real LEDs.
- Every concept comes at both 64x16 and 64x64. Five are native strips (Fox hunt, Jamming storm, Modulation waveform, Transmitter beacon, Analog tuner) and five are native squares (Satellite uplink, Digital sync grid, Frequency hopping, Spectrum analyzer, APRS decode). Each has a hand-laid version at the other size.
- All loop seamlessly and fit the BLE upload budget. The APRS screen shows example data only (placeholder callsign N0CALL).
- Pick **RF / Signal** in the Presets family filter to find them.

### Updates
- This is the first release that installs through the in-app updater from 0.2.2.

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
