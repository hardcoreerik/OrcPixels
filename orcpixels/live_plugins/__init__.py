"""Live-source plugins: each module defines SCREENS (Screen instances with their feeds and
`category`); load() registers them into livedata_screens.SCREENS. Add a module here and
list it in BUILTIN (explicit, so the frozen Windows build includes it) - nothing else
changes: the engine, the API, the Live tab and the previews pick it up."""
from __future__ import annotations

import importlib
import logging

log = logging.getLogger("orcpixels.plugins")
BUILTIN = ("sports", "entertainment", "world_weather", "space_more", "markets_more", "ham_spots", "motorsport",
           "games", "local_more", "status_board")


def load(screens: dict) -> list[str]:
    added = []
    for name in BUILTIN:
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except Exception as e:  # noqa: BLE001  (a broken plugin must not take Live Data down)
            log.warning("live plugin %s failed to load: %s", name, e)
            continue
        for s in getattr(mod, "SCREENS", []):
            if s.key not in screens:
                screens[s.key] = s
                added.append(s.key)
    return added
