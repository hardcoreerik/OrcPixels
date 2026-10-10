"""Branding config: generic app icons (Codex/Claude/Grok) and per-project icons.

Two separate files so app identity and project identity never mix:
  config/apps.json      app key  -> {display, icon, accent, process_names}
  config/projects.json  repo key -> {display, icon, accent, banner, match:[globs]}

Paths are relative to the OrcPixels root. Phase 2 watcher flow:
  app = find_app(process_name); proj = find_project(repo_name)
  frame = status_frame(app, proj, branch)  -> SignClient.send_image(frame)
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
from .paths import CONFIG, config_file  # noqa: E402
BRAND_ICON = ROOT / "assets" / "orc_16.png"


def _hex(c: str | None, default=(255, 255, 255)):
    return tuple(bytes.fromhex(c.lstrip("#"))) if c else default


@dataclass
class Brand:
    key: str
    display: str
    icon: Path | None = None
    accent: tuple = (255, 255, 255)
    banner: Path | None = None
    match: list[str] = field(default_factory=list)
    process_names: list[str] = field(default_factory=list)

    def icon_image(self) -> Image.Image | None:
        return Image.open(self.icon).convert("RGBA") if self.icon and self.icon.exists() else None

    def banner_image(self) -> Image.Image | None:
        return Image.open(self.banner).convert("RGB") if self.banner and self.banner.exists() else None


def _load(name: str) -> dict[str, Brand]:
    p = config_file(name)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for key, v in raw.items():
        if key.startswith("_"):
            continue
        out[key] = Brand(
            key=key, display=v.get("display", key),
            icon=(ROOT / v["icon"]) if v.get("icon") else None,
            accent=_hex(v.get("accent")),
            banner=(ROOT / v["banner"]) if v.get("banner") else None,
            match=v.get("match", [key]),
            process_names=[s.lower() for s in v.get("process_names", [])],
        )
    return out


@lru_cache(maxsize=1)
def projects() -> dict[str, Brand]:
    return _load("projects.json")


@lru_cache(maxsize=1)
def apps() -> dict[str, Brand]:
    return _load("apps.json")


def reload():
    projects.cache_clear()
    apps.cache_clear()


def find_project(repo: str) -> Brand | None:
    """Exact key match first, then case-insensitive glob patterns (so worktree
    folders like 'OrcSDR-radio-refactor' still map to OrcSDR)."""
    ps = projects()
    if repo in ps:
        return ps[repo]
    low = repo.lower()
    for b in ps.values():
        if any(fnmatch.fnmatch(low, m.lower()) for m in b.match):
            return b
    return None


def find_app(process_name: str) -> Brand | None:
    low = process_name.lower()
    for b in apps().values():
        if low in b.process_names or low == b.key.lower():
            return b
    return None


def status_frame(app: Brand | None, project: Brand | None, branch: str = "",
                 width: int = 64, height: int = 16) -> Image.Image:
    """Icon (project icon, else app icon, else OrcPixels) + two tiny lines:
    project name in the project accent, then 'App/branch'."""
    icon = (project and project.icon_image()) or (app and app.icon_image())
    if icon is None and BRAND_ICON.exists():
        icon = Image.open(BRAND_ICON).convert("RGBA")
    img = Image.new("RGB", (width, height))
    x = 0
    if icon is not None:
        icon.thumbnail((height, height))
        img.paste(icon, (0, 0), icon)
        x = height + 1
    d = ImageDraw.Draw(img)
    small = ImageFont.load_default()
    title = project.display if project else "?"
    sub = "/".join(s for s in [(app.display if app else ""), branch] if s)
    d.text((x, -2), title, fill=project.accent if project else (255, 255, 255), font=small)
    d.text((x, 7), sub, fill=app.accent if app else (150, 150, 150), font=small)
    return img


def splash_gif(text: str = "OrcPixels", frame_ms: int = 80, step: int = 2) -> tuple[bytes, list[Image.Image]]:
    """OrcPixels splash: orc_16 icon fixed left + text scrolling in the right 47px.

    step=2 px/frame halves the GIF (~60 frames, ~13 KB) since BLE upload runs ~1.1 KB/s
    at the reference 244-byte chunk size."""
    from .render import scrolling_gif
    icon = Image.open(BRAND_ICON).convert("RGB") if BRAND_ICON.exists() else None
    stops = [(80, 255, 40), (140, 255, 120), (80, 255, 40)]
    return scrolling_gif(text, icon, step=step, frame_ms=frame_ms, stops=stops)


def project_frame(key: str, kind: str = "banner", size: tuple[int, int] | None = None) -> Image.Image:
    """64x16 frame for a project: its banner, banner_alt ('wave'), or icon + name.
    size=(64, 64): the project's icon 2x + name, with its 64x16 banner along the bottom."""
    if size is not None and tuple(size) == (64, 64) and kind in ("banner", "wave", "icon"):
        return project_frame64(key, kind)
    import json as _json
    b = projects().get(key) or find_project(key)
    if b is None:
        raise KeyError(f"unknown project {key!r} (see config/projects.json)")
    raw = _json.loads(config_file("projects.json").read_text(encoding="utf-8")).get(b.key, {})
    if kind not in ("banner", "wave", "icon"):          # allow a preset name as kind
        from .presets import preview_image
        return preview_image(kind)
    path = {"banner": raw.get("banner"), "wave": raw.get("banner_alt")}.get(kind)
    if path and (ROOT / path).exists():
        return Image.open(ROOT / path).convert("RGB")
    img = Image.new("RGB", (64, 16))
    ic = b.icon_image()
    if ic:
        img.paste(ic, (0, 0), ic)
    from .render import _font
    ImageDraw.Draw(img).text((17, 0), b.display, fill=b.accent, font=_font(16))
    return img


def project_frame64(key: str, kind: str = "banner") -> Image.Image:
    import json as _json

    from .livedata_screens import big_w
    from .render import _font
    b = projects().get(key) or find_project(key)
    if b is None:
        raise KeyError(f"unknown project {key!r} (see config/projects.json)")
    raw = _json.loads(config_file("projects.json").read_text(encoding="utf-8")).get(b.key, {})
    img = Image.new("RGB", (64, 64))
    ic = b.icon_image()
    if ic:
        ic2 = ic.resize((ic.width * 2, ic.height * 2), Image.Resampling.NEAREST)
        img.paste(ic2, ((64 - ic2.width) // 2, 1), ic2)
    name = b.display
    d = ImageDraw.Draw(img)
    if big_w(name) <= 64:
        d.text(((64 - big_w(name)) // 2, 32), name, fill=b.accent, font=_font(16))
    else:
        from .tinyfont import draw_text, text_width
        n = name.upper()
        while n and text_width(n) > 64:
            n = n[:-1]
        draw_text(img, (64 - text_width(n)) // 2, 38, n, b.accent)
    path = {"banner": raw.get("banner"), "wave": raw.get("banner_alt")}.get(kind) or raw.get("banner")
    if path and (ROOT / path).exists():
        ban = Image.open(ROOT / path).convert("RGB")
        if ban.size != (64, 16):
            ban.thumbnail((64, 16))
        img.paste(ban, ((64 - ban.width) // 2, 48))
    return img


def default_preset(key: str) -> str | None:
    """Preset name to play for a project (config/projects.json 'default_preset')."""
    import json as _json
    b = projects().get(key) or find_project(key)
    if b is None:
        return None
    return _json.loads(config_file("projects.json").read_text(encoding="utf-8")).get(b.key, {}).get("default_preset")
