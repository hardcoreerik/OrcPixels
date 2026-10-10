"""Wire formats for network pixel protocols (pure functions, tested byte by byte).

DDP      http://www.3waylabs.com/ddp/  (UDP 4048): 10-byte header
         [flags=0x40 ver1 | 0x01 push on the last packet][seq 1..15][type 0x0B = RGB 8 bit]
         [dest id 1][offset u32 BE][length u16 BE] + data (<= 1440 bytes per packet)
E1.31    ANSI E1.31-2018 sACN data packet (UDP 5568), 126-byte header + up to 512 slots
Art-Net  ArtDmx (UDP 6454): "Art-Net\\0", OpCode 0x5000 LE, ProtVer 14, seq, physical,
         SubUni, Net, length BE (even), data
WLED     UDP realtime (UDP 21324) DNRGB: [4][timeout s][start index u16 BE] + RGB (<= 489 LEDs)
"""
from __future__ import annotations

import struct
import uuid

DDP_PORT, E131_PORT, ARTNET_PORT, WLED_RT_PORT = 4048, 5568, 6454, 21324
DDP_MAX = 1440
DDP_TYPE_RGB24, DDP_ID_DISPLAY = 0x0B, 1


def ddp_packets(data: bytes, seq: int, dest: int = DDP_ID_DISPLAY, max_len: int = DDP_MAX) -> list[bytes]:
    """One frame -> DDP packets; the last one carries PUSH. seq: 1..15."""
    seq = (seq - 1) % 15 + 1
    out, n = [], max(1, -(-len(data) // max_len))
    for i in range(n):
        chunk = data[i * max_len:(i + 1) * max_len]
        flags = 0x40 | (0x01 if i == n - 1 else 0x00)
        out.append(struct.pack(">BBBBIH", flags, seq, DDP_TYPE_RGB24, dest, i * max_len, len(chunk)) + chunk)
    return out


E131_ACN_ID = b"ASC-E1.17\x00\x00\x00"


def e131_packet(universe: int, slots: bytes, seq: int, cid: bytes, source: str = "OrcPixels",
                priority: int = 100) -> bytes:
    if not 1 <= universe <= 63999:
        raise ValueError("E1.31 universe 1..63999")
    if len(slots) > 512:
        raise ValueError("at most 512 slots per universe")
    if len(cid) != 16:
        raise ValueError("CID must be 16 bytes")
    n = len(slots)
    total = 126 + n
    name = source.encode("utf-8")[:63].ljust(64, b"\x00")
    root = struct.pack(">HH12sHI16s", 0x0010, 0x0000, E131_ACN_ID, 0x7000 | (total - 16), 0x00000004, cid)
    framing = struct.pack(">HI64sBHBBH", 0x7000 | (total - 38), 0x00000002, name, priority, 0, seq & 0xFF, 0,
                          universe)
    dmp = struct.pack(">HBBHHHB", 0x7000 | (total - 115), 0x02, 0xA1, 0x0000, 0x0001, n + 1, 0x00)
    return root + framing + dmp + slots


def e131_multicast(universe: int) -> str:
    return f"239.255.{(universe >> 8) & 0xFF}.{universe & 0xFF}"


def new_cid() -> bytes:
    return uuid.uuid4().bytes


def artdmx_packet(universe: int, data: bytes, seq: int, physical: int = 0) -> bytes:
    """universe: 15-bit Port-Address (Net << 8 | SubNet << 4 | Universe)."""
    if not 0 <= universe <= 0x7FFF:
        raise ValueError("Art-Net port-address 0..32767")
    if len(data) > 512:
        raise ValueError("at most 512 channels")
    if len(data) % 2:
        data += b"\x00"
    if len(data) < 2:
        data = data.ljust(2, b"\x00")
    return (b"Art-Net\x00" + struct.pack("<H", 0x5000) + struct.pack(">H", 14)
            + bytes([seq & 0xFF, physical & 0xFF, universe & 0xFF, (universe >> 8) & 0x7F])
            + struct.pack(">H", len(data)) + data)


def split_universes(data: bytes, per_universe: int = 510) -> list[bytes]:
    """Pixel bytes -> per-universe channel blocks (510 = 170 RGB pixels, no pixel split)."""
    per_universe -= per_universe % 3
    return [data[i:i + per_universe] for i in range(0, len(data), per_universe)] or [b""]


WLED_DNRGB_MAX = 489


def wled_dnrgb_packets(data: bytes, timeout_s: int = 2) -> list[bytes]:
    px = len(data) // 3
    out = []
    for start in range(0, px, WLED_DNRGB_MAX):
        n = min(WLED_DNRGB_MAX, px - start)
        out.append(bytes([4, timeout_s & 0xFF]) + struct.pack(">H", start) + data[start * 3:(start + n) * 3])
    return out


# ---------------------------------------------------------------- serial
def adalight_frame(data: bytes) -> bytes:
    """Adalight: 'Ada' + count_hi + count_lo + (hi ^ lo ^ 0x55) + RGB; count = LEDs - 1."""
    n = len(data) // 3 - 1
    hi, lo = (n >> 8) & 0xFF, n & 0xFF
    return b"Ada" + bytes([hi, lo, hi ^ lo ^ 0x55]) + data


def tpm2_frame(data: bytes) -> bytes:
    """TPM2 data frame: 0xC9 0xDA size_hi size_lo data 0x36."""
    if len(data) > 0xFFFF:
        raise ValueError("TPM2 frame too large")
    return bytes([0xC9, 0xDA]) + struct.pack(">H", len(data)) + data + b"\x36"
