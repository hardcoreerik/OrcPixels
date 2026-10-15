"""OrcPixels: control an iPixel Color BLE LED sign from Windows."""
from .ble_sign import SignClient, scan, DEFAULT_ADDRESS, NAME_PREFIX
from .protocol import TextAnimation

__all__ = ["SignClient", "scan", "DEFAULT_ADDRESS", "NAME_PREFIX", "TextAnimation"]
__version__ = "0.2.4"
