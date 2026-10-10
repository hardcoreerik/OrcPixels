"""iPixel Color BLE protocol frame builders (pure functions, no BLE I/O).

Opcodes and framing are taken from lucagoc/pypixelcolor (MIT), which is a
reverse-engineering of the Android "iPixel Color" app:
  https://github.com/lucagoc/pypixelcolor  (checked at commit 7ff1f5c, 2026-09-08)

Transport summary (from pypixelcolor lib/transport/*):
  * Write to 0000fa02, notifications on 0000fa03.
  * A "window" (max 12 KiB of payload) is written in <=244-byte chunks.
  * Device ACKs a window with a notify frame starting 0x05; byte[4] = 0/1
    (window accepted) or 3 (whole transfer complete).
"""
from __future__ import annotations

import binascii
import io
import unicodedata
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

WRITE_UUID = "0000fa02-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fa03-0000-1000-8000-00805f9b34fb"

WINDOW_SIZE = 12 * 1024
CHUNK_SIZE = 244

FONT_DIR = Path(__file__).resolve().parent / "fonts"
UNIFONT_PATH = FONT_DIR / "unifont.otf"

# device-info byte[4] -> (width, height), from pypixelcolor lib/device_info.py
DEVICE_TYPE_SIZES = {
    128: (64, 64), 129: (32, 32), 130: (32, 16), 131: (64, 16), 132: (96, 16),
    133: (64, 20), 134: (128, 32), 135: (144, 16), 136: (192, 16), 137: (48, 24),
    138: (64, 32), 139: (96, 32), 140: (128, 32), 141: (96, 32), 142: (160, 32),
    143: (192, 32), 144: (256, 32), 145: (320, 32), 146: (384, 32), 147: (448, 32),
}


class TextAnimation(IntEnum):
    STATIC = 0
    SCROLL_LEFT = 1
    SCROLL_RIGHT = 2
    SCROLL_UP = 3      # 32x32 only
    SCROLL_DOWN = 4    # 32x32 only
    BLINK = 5
    FADE = 6
    SNOWFLAKE = 7


@dataclass
class Frame:
    """One transport window: bytes to write + whether the device ACKs it."""
    data: bytes
    requires_ack: bool = True


@dataclass
class Command:
    name: str
    frames: list[Frame]
    expects_response: bool = False   # e.g. device info reply
    wait_final_ack: bool = False


def _single(name: str, data: Iterable[int], requires_ack: bool = True) -> Command:
    return Command(name, [Frame(bytes(data), requires_ack)])


# ---------------------------------------------------------------- simple cmds
def get_device_info(hour: int, minute: int, second: int, language: int = 0) -> Command:
    c = _single("device_info", [8, 0, 1, 0x80, hour, minute, second, language], requires_ack=False)
    c.expects_response = True
    return c


def set_brightness(level: int) -> Command:
    if not 0 <= int(level) <= 100:
        raise ValueError("brightness must be 0..100")
    return _single("brightness", [5, 0, 4, 0x80, int(level)])


def set_power(on: bool) -> Command:
    return _single("power", [5, 0, 7, 1, 1 if on else 0])


def set_orientation(orientation: int) -> Command:
    if not 0 <= int(orientation) <= 3:
        raise ValueError("orientation must be 0..3")
    return _single("orientation", [5, 0, 6, 0x80, int(orientation)])


# ------------------------------------------------ stored slots / program list
# Sources (all reverse-engineered from the Android "iPixel Color" app):
#  [PPC] lucagoc/pypixelcolor @7ff1f5c  commands/show_slot.py, commands/delete.py,
#        commands/send_image.py::_build_send_plan, commands/send_text/__init__.py
#  [DK]  DonKracho/ESPHome-component-iPixel-ble @1c29784  docs/IPIXEL_COMMANDS_DEMYSTIFIED.md
#        ("Set Program List", "Delete Program List"), components/ipixel_ble/iPixelCommands.cpp
#        setProgramList()/delProgramList(), ipixel_ble.cpp on_play_switch()/get_slot()
#  [IPC] sdolphin-JP/ipixel-ctrl  docs/DeviceCommands.md (0x0002/0x0003 SCR_NO, 0x8007, 0x8008),
#        ipixel_ctrl/commands/set_prg_mode.py, erase_data.py
#
# Save slot = byte 14 (0-based) of the first window of a PNG/GIF/text transfer
# (the "tail" 2nd byte in _windowed()):  [IPC] SCR_NO 0x01-0x64 = PRG (program) slots,
# 0x65 = DIY/temporary, 0x6F-0x77 = "Remocon" screens 1-9 (selected by 0x8007).
# [DK]: "slot numbers may have a value range from 1 to 100 ... 0 and numbers above 100 are
# reserved. For these numbers the uploaded effects are applied immediately and the program
# list execution halts."  -> our live sends use slot 0 and therefore STOP a running playlist.
PROGRAM_SLOT_MIN = 1
PROGRAM_SLOT_MAX = 100      # 0x64, [DK] + [IPC]; no device query for the count exists


def _check_slots(slots: Iterable[int]) -> list[int]:
    out = [int(s) for s in slots]
    if not out:
        raise ValueError("need at least one slot")
    if len(out) > PROGRAM_SLOT_MAX:
        raise ValueError(f"at most {PROGRAM_SLOT_MAX} slots")
    bad = [s for s in out if not PROGRAM_SLOT_MIN <= s <= PROGRAM_SLOT_MAX]
    if bad:
        raise ValueError(f"program slots must be {PROGRAM_SLOT_MIN}..{PROGRAM_SLOT_MAX}, got {bad}")
    return out


def set_program_list(slots: Iterable[int]) -> Command:
    """0x8008 set_program_list / set_prg_mode: the device cycles the stored slots in this
    order, endlessly, on its own ([DK] "shown in a endless sequence loop").

        LL LL 08 80 NN NN s1 s2 ... sN      LL = 6 + N (LE u16), NN = N (LE u16)
    e.g. slots 1,2,3 -> 09 00 08 80 03 00 01 02 03.  Reply 05 00 08 80 01 (OK) / 00 (NG).
    There is NO per-item duration field in any source; the device decides how long each
    item is shown.  (pypixelcolor's show_slot is this command with a single slot.)"""
    s = _check_slots(slots)
    body = bytes([0x08, 0x80]) + len(s).to_bytes(2, "little") + bytes(s)
    return Command("program_list", [Frame((len(body) + 2).to_bytes(2, "little") + body, True)])


def show_slot(n: int) -> Command:
    """[PPC] show_slot: 07 00 08 80 01 00 NN == set_program_list([n]).
    [PPC] note: "If the slot is empty, the device will cycle through available slots"."""
    return _single("show_slot", [7, 0, 8, 0x80, 1, 0, int(n) & 0xFF])


def delete_slot(n: int) -> Command:
    """[PPC] delete: 07 00 02 01 01 00 NN == delete_slots([n])."""
    return _single("delete_slot", [7, 0, 2, 1, 1, 0, int(n) & 0xFF])


def delete_slots(slots: Iterable[int]) -> Command:
    """0x0102 del_program_list ([DK]/[IPC] erase_data): LL LL 02 01 NN NN s1..sN.
    Only explicit slots 1..100 are allowed here: an EMPTY list (04 00 02 01 00 00) or
    ipixel-ctrl's --all (0xFF slots) erases everything and is deliberately not buildable.
    [DK]: stop the program list first; deleting slots in a running upload may crash it."""
    s = _check_slots(slots)
    body = bytes([0x02, 0x01]) + len(s).to_bytes(2, "little") + bytes(s)
    return Command("delete_slots", [Frame((len(body) + 2).to_bytes(2, "little") + body, True)])


def get_firmware_versions() -> Command:
    """[DK] 04 00 05 80 -> 12-byte reply 0c 00 05 80 + MCU/BLE versions. Read-only."""
    c = _single("firmware_versions", [4, 0, 5, 0x80], requires_ack=False)
    c.expects_response = True
    return c


def parse_ack(data: bytes) -> dict:
    """5-byte fa03 ack: 05 00 <cmd lo> <cmd hi> <status>.  Observed on this sign:
    0500068001 (orientation OK), 0500030003 (GIF transfer complete), 0500000103 (text).
    status for transfers: 1 = window accepted, 3 = complete, 0 = NG / CRC error ([DK])."""
    if len(data) < 5 or data[0] != 0x05:
        return {"cmd": None, "status": None, "raw": data.hex()}
    return {"cmd": data[2] | (data[3] << 8), "status": data[4], "raw": data[:5].hex()}


def factory_clear_storage() -> Command:
    """DANGER: pypixelcolor documents this as wiping all stored content AND settings."""
    return _single("clear_storage", [4, 0, 3, 0x80])


def parse_device_info(resp: bytes) -> dict:
    if len(resp) < 5:
        raise ValueError(f"device info reply too short: {resp.hex()}")
    w, h = DEVICE_TYPE_SIZES.get(resp[4], (64, 64))
    return {
        "device_type": resp[4], "width": w, "height": h,
        "password_flag": resp[10] if len(resp) >= 11 else 255,
        # byte[8]: undocumented in pypixelcolor/[DK] (they only use byte 4). Observed on this
        # sign (2026-09-30): 00 before any slot write, 03 after storing slots 1..3, still 03
        # with a 1-slot program list -> probably the number of stored program slots. Hint only.
        "stored_slots_hint": resp[8] if len(resp) >= 9 else None,
        "raw": resp.hex(),
    }


def classify_notify(data: bytes) -> str | None:
    """Return 'window', 'done' or None for a fa03 notification."""
    if len(data) >= 5 and data[0] == 0x05:
        if data[4] in (0, 1):
            return "window"
        if data[4] == 3:
            return "done"
    return None


# ------------------------------------------------------- windowed transfers
def _windowed(name: str, kind: bytes, payload: bytes, tail: bytes) -> Command:
    """kind = 2 bytes after the length prefix: text=00 01, png=02 00, gif=03 00."""
    size = len(payload).to_bytes(4, "little")
    crc = (binascii.crc32(payload) & 0xFFFFFFFF).to_bytes(4, "little")
    frames = []
    for idx, pos in enumerate(range(0, len(payload), WINDOW_SIZE)):
        option = 0x00 if idx == 0 else 0x02
        body = kind + bytes([option]) + size + crc + tail + payload[pos:pos + WINDOW_SIZE]
        frames.append(Frame((len(body) + 2).to_bytes(2, "little") + body, True))
    return Command(name, frames)


def image_to_png(img: Image.Image, width: int = 64, height: int = 16) -> bytes:
    """Flatten alpha to black, fit/crop to the panel size, return PNG bytes."""
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    rgb = Image.alpha_composite(bg, rgba).convert("RGB")
    if rgb.size != (width, height):
        rgb = fit_image(rgb, width, height)
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    return buf.getvalue()


def fit_image(img: Image.Image, width: int, height: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((width, height), Image.Resampling.LANCZOS)
    out = Image.new("RGB", (width, height), (0, 0, 0))
    out.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
    return out


def send_png(png: bytes, save_slot: int = 0) -> Command:
    return _windowed("send_image", b"\x02\x00", png, bytes([0x00, save_slot & 0xFF]))


def send_gif(gif: bytes, save_slot: int = 0) -> Command:
    return _windowed("send_gif", b"\x03\x00", gif, bytes([0x02, save_slot & 0xFF]))


# --------------------------------------------------------------------- text
def _reverse_bits(data: bytes) -> bytes:
    return bytes(int(f"{b:08b}"[::-1], 2) for b in data)


def _encode_glyph(img: Image.Image) -> bytes:
    """Row-major bitmap, MSB = leftmost pixel, 1 or 2 bytes per row (16px-high mode)."""
    w, h = img.size
    out = bytearray()
    for y in range(h):
        v = 0
        for x in range(w):
            if img.getpixel((x, y)) > 0:
                v |= 1 << (15 - x)
        if w <= 8:
            out += (v >> 8).to_bytes(1, "big")
        else:
            out += v.to_bytes(2, "big")
    return bytes(out)


def render_glyph(ch: str, font: ImageFont.FreeTypeFont, char_height: int = 16,
                 threshold: int = 128) -> tuple[bytes, int]:
    tmp = ImageDraw.Draw(Image.new("L", (100, char_height)))
    x0, _, x1, _ = tmp.textbbox((0, 0), ch, font=font)
    wide = unicodedata.east_asian_width(ch) in ("W", "F") or (x1 - x0) > 10
    slot_w = 16 if wide else 8
    img = Image.new("L", (slot_w, char_height), 0)
    ImageDraw.Draw(img).text((0, 0), ch, fill=255, font=font)
    img = img.point(lambda p: 255 if p > threshold else 0, mode="L")
    return _encode_glyph(img), slot_w


def load_font(path: str | Path | None = None, size: int = 16) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path or UNIFONT_PATH), size)


def _rgb(color: str) -> bytes:
    b = bytes.fromhex(color.lstrip("#"))
    if len(b) != 3:
        raise ValueError(f"color must be RRGGBB, got {color!r}")
    return b


def send_text(text: str, *, color: str = "ffffff", bg_color: str | None = None,
              animation: int = TextAnimation.STATIC, speed: int = 80, rainbow: int = 0,
              save_slot: int = 0, font: ImageFont.FreeTypeFont | None = None,
              char_height: int = 16) -> Command:
    """Native text command (device renders/scrolls glyph bitmaps we send).

    16-px-high panels only (this sign is 64x16); 32-px glyph mode not implemented.
    Emojis are not supported here (pypixelcolor sends them as JPEG blocks).
    """
    if not 1 <= len(text) <= 255:
        raise ValueError("text length must be 1..255")
    if char_height != 16:
        raise NotImplementedError("only 16px-high glyphs are implemented")
    font = font or load_font(size=char_height)
    col = _rgb(color)
    chars = bytearray()
    for ch in (text[::-1] if animation == TextAnimation.SCROLL_RIGHT else text):
        glyph, w = render_glyph(ch, font, char_height)
        opcode = 0x01 if w > 8 else 0x00
        chars += bytes([opcode]) + col + _reverse_bits(glyph)
    props = bytearray([0x00, 0x01, 0x01, int(animation) & 0xFF, int(speed) & 0xFF, int(rainbow) & 0xFF])
    props += col
    props += (b"\x01" + _rgb(bg_color)) if bg_color else b"\x00\x00\x00\x00"
    payload = bytes([len(text)]) + bytes(props) + bytes(chars)
    return _windowed("send_text", b"\x00\x01", payload, bytes([0x00, save_slot & 0xFF]))
