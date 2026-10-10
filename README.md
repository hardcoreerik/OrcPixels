# OrcPixels

A local studio and live-data driver for **iPixel Color** Bluetooth LED signs (the cheap
`LED_BLE_*` panels: 64x16 flexible strips, 64x64 matrices and friends).

* Web UI on your own machine (no cloud, no account): presets, a pixel creator, image / GIF
  converter, text, brightness and power, saved playlists on the sign.
* **Several signs at once**: each display has its own size, rotation, settings and playlists.
* **Curated layouts per size**: every preset and live screen has a hand-made 64x64 version as
  well as the 64x16 one, and every animation loops seamlessly.
* **Live Data** screens: weather, forecast, alerts, air quality, space weather, HF bands, ISS,
  earthquakes, aircraft, tides, river level, sun and moon, clocks, markets, crypto, news
  headlines, Hacker News, GitHub, X, and FT8 / FT4 decodes from an OrcSDR receiver.
* **OrcSDR Live**: spectrum and waterfall from an OrcSDR receiver, streamed to the sign.
* Optional **AI status / buddy** (Windows): shows which AI coding app is in front (Codex,
  Claude, Grok, Cursor), its repo / branch / worktree, and what the agent is doing.

Runs on Windows, macOS and Linux (Python 3.11+, Bluetooth LE via [bleak](https://github.com/hbldh/bleak)).
The AI status feature currently needs Windows; elsewhere it reports itself unavailable.

## Install

```powershell
# Windows
git clone https://github.com/hardcoreerik/OrcPixels.git OrcPixels
cd OrcPixels
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Shortcut        # add -WithAI for the AI feature
```

```bash
# macOS / Linux
git clone https://github.com/hardcoreerik/OrcPixels.git OrcPixels
cd OrcPixels
./install.sh --shortcut
```

The installers check for Python 3.11+, create `.venv`, install `requirements.txt`
(`-WithAI` adds `requirements-ai.txt`: pywin32 + psutil), copy the example configs you don't
have yet, and optionally add a desktop shortcut. They never add autostart, never overwrite
your config, and are safe to re-run.

## First run

1. Start the server: `.venv\Scripts\python -m orcpixels serve` (Windows) or
   `.venv/bin/python -m orcpixels serve`, or use the shortcut / `tools/launch_orcpixels.*`.
2. Open <http://127.0.0.1:8765>. The server binds to 127.0.0.1 only.
3. Close the iPixel phone app (a sign takes one BLE connection), power the sign on, then
   **Scan / add display** in the status panel, pick your sign and give its size. Signs are
   only ever connected by the address you saved, never "whatever LED_BLE_* is nearby".
4. Optional, in the **Live** tab's feed settings: home latitude / longitude, a short place
   tag, time zone, and the stations you want (NWS station, tide station, USGS river gauge,
   GitHub user, RSS URLs, X handle and token, OrcSDR URL / serial port for FT8).
   Screens whose source isn't set stay on a "not configured" screen.

## Configuration

Everything machine- or person-specific lives in gitignored files under `config/`:

| file | what | example |
|---|---|---|
| `config/displays.json` | your signs: id, label, BLE address, size, rotation, autoconnect | `config/displays.example.json` |
| `config/settings.json` | shared settings: location, time zone, feeds, OrcSDR URL, AI switch | `config/settings.example.json` |
| `config/displays/<id>/` | per-display settings overlay, playlists and slot record | (written by the UI) |
| `config/apps.local.json` | per-machine overrides of `config/apps.json` (AI app detection) | `config/apps.local.example.json` |
| `.env` | optional environment variables (tokens, Python path, port) | `.env.example` |

Tracked and generic: `config/apps.json` (how AI apps are recognised) and
`config/projects.json` (per-repo icons / colours for the AI status screen).

Location settings: `home_lat`, `home_lon`, `home_label`, `timezone` (`local` by default; also
`utc`, `us-pacific` / `us-mountain` / `us-central` / `us-eastern`, or `+HH:MM`),
`livedata_nws_station` and `livedata_alerts_area` (US; derived from your location if blank),
`livedata_quake_radius_km`, `livedata_tide_station`, `livedata_usgs_site`,
`livedata_github_user`, plus optional `*_label` tags for the screens. NWS screens only work in
the US; the rest work anywhere.

## AI status / buddy (optional, Windows)

When **Auto status** is on, OrcPixels looks at the foreground window (pywin32 + psutil),
recognises Codex, Claude, Grok or Cursor from `config/apps.json`, finds the project folder
from that app's own local session files, and reads repo / branch / worktree with `git`.
**Buddy** adds a short line about what the agent is doing (reading, editing, running tests),
from the tail of the app's local session log. Text is redacted before it goes to the sign
(tokens, keys, e-mail addresses, URLs, long paths). Nothing leaves your machine.

These apps' session files are private, undocumented formats that change between versions;
if a reader stops matching, OrcPixels logs one warning and shows the app name only. Switch the
whole feature off with the checkbox in the UI or `"ai_enabled": false` in `config/settings.json`.
The UI shows what is available on your machine (`GET /api/ai/state` -> `capabilities`).

## CLI

```
python -m orcpixels scan                         # list LED_BLE_* devices
python -m orcpixels info                         # device info (type 131 = 64x16, 64x64 panels report their own type)
python -m orcpixels send-text "HELLO"            # native text
python -m orcpixels send-text "Hi there" --anim scroll_left --speed 60 -c 00ff80
python -m orcpixels image icon.png       # any image, fitted to the sign
python -m orcpixels test-image                   # built-in 16x16 icon + "ORC" + rainbow
python -m orcpixels status "Codex" "OrcSDR/main" --icon app.png   # phase-2 layout
python -m orcpixels splash                       # orc_16 icon + scrolling "OrcPixels" (animated GIF)
python -m orcpixels project OrcSDR --kind banner  # banner | wave | icon  (from config/projects.json)
python -m orcpixels gif anim64x16.gif
python -m orcpixels brightness 60                # 0..100
python -m orcpixels power off|on
python -m orcpixels clear                        # sends a black frame (does NOT wipe storage)
python -m orcpixels -v ...                       # -v / -vv for BLE + notify logs
python -m orcpixels -a "" ...                    # match by name prefix LED_BLE_ instead of address
```

## Web API

The UI is a single static page over a JSON API, e.g. `GET /api/status`,
`POST /api/connect`, `POST /api/text`, `POST /api/image`, `POST /api/brightness {level}`,
`GET /api/displays`, `POST /api/displays/select {id}`, `GET /api/livedata/screens`,
`GET /api/ai/state`. Any route can be sent to a specific display with `/d/<id>/api/...`, the
`X-Display` header or `?display=<id>`.

## Development

```
.venv/bin/python -m pytest -q          # offline tests (no sign, no network)
python tools/led64_sheet.py            # contact sheets of the 64x64 set
python tools/loopcheck.py              # seamless-loop check for presets
```

Layout: `orcpixels/` (protocol, BLE client, web app, renderers, live data in `sources/`),
`presets/` and `presets64/` (generated art + metadata), `tools/` (generators, sheets,
probes), `tests/` (fixtures are synthetic or public API samples).

## Protocol source

Opcodes/framing follow [lucagoc/pypixelcolor](https://github.com/lucagoc/pypixelcolor)
(MIT, reverse-engineered from the iPixel Color app; checked at commit 7ff1f5c).
Write `0000fa02` (with response, <=244-byte chunks, 12 KiB windows), ACKs on
`0000fa03` as `05 00 .. .. XX` with XX=0/1 (window ok) or 3 (transfer done).

| command | bytes |
|---|---|
| device info | `08 00 01 80 hh mm ss 00` -> reply byte[4] = type (131 = 64x16) |
| brightness | `05 00 04 80 LL` |
| power | `05 00 07 01 00/01` |
| orientation | `05 00 06 80 0..3` |
| PNG | `len16LE 02 00 opt size32LE crc32LE 00 slot <png>` |
| GIF | `len16LE 03 00 opt size32LE crc32LE 02 slot <gif>` |
| text | `len16LE 00 01 opt size crc 00 slot` + `[n] 00 01 01 anim speed rainbow RGB bg(4)` + per char `op RGB bitmap` |
| storage wipe | `04 00 03 80` (exposed as `protocol.factory_clear_storage()` only; not used, not tested) |

## Licence

MIT, copyright (c) 2026 Oregon Radio Company LLC. See `LICENSE`, and `NOTICE` for the bundled
font and data (GNU Unifont, AD1C cty.dat, GeoNames, Natural Earth) and the terms of the online
APIs the Live Data screens use. Not affiliated with the makers of iPixel Color.

## Windows installer

Download `OrcPixels-Setup-x.y.z.exe` from the [Releases](https://github.com/hardcoreerik/OrcPixels/releases) page. It bundles Python (PyInstaller build), so nothing else is needed.

- Installs to Program Files with a Start Menu entry, an optional desktop shortcut and an uninstaller.
- Wizard asks for a **Library folder** (default `Documents\OrcPixels Library`) and offers **Start OrcPixels when Windows starts** (off by default; a per-user Run key the uninstaller removes).
- Settings, displays, playlists and your own presets live in `%APPDATA%\OrcPixels`, never in Program Files.
- Running OrcPixels starts the server in the background with a tray icon: **Open OrcPixels**, **Settings**, **Server address** (click to copy), **Start with Windows**, **Quit** (clean shutdown).
- The server listens on 127.0.0.1 only. Set `listen_lan: true` (and ideally `lan_token`) in settings to reach it from other devices; LAN clients then need the token (`X-OrcPixels-Token` header or `?token=`).

Build it yourself: `powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1` (needs Python 3.11+ and Inno Setup 6). Pushing a `v*` tag builds it in GitHub Actions and attaches it to a release.

## Supported displays

| Display | Driver | Transport | Status |
|---|---|---|---|
| iPixel Color 64x16 / 64x64 (BLE) | `ipixel` | Bluetooth LE | stable (tested) |
| Virtual panel (any size, browser preview + PNG/GIF) | `virtual` | none | stable |
| WLED matrices | `wled` | JSON API + DDP or UDP realtime | experimental, untested on hardware |
| ESP32 HUB75 panels (WLED-HUB75, FPP, ...) | `udp` | DDP / E1.31 (sACN) / Art-Net | experimental, untested on hardware |
| Divoom Pixoo 64 | `pixoo` | local HTTP | experimental, untested on hardware |
| USB/serial matrices (Arduino, Teensy, ESP32) | `serial` | Adalight / TPM2 | experimental, untested on hardware |
| Tidbyt / Tronbyt | `tidbyt` | HTTPS push API | experimental, untested on hardware |

Profiles live in `profiles/` (see `profiles/README.md`); add a panel with **Settings > Add display**. Other panel sizes render the closest curated set and are auto-fitted.
