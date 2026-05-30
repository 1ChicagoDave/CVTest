"""Thin wrapper around pixelblaze-client so the mapping loop can be tested
against a fake, and so all hardware control lives behind one small interface.

The Automap.epe pattern exports a single variable, `pixel`; lighting LED N is
just setting that variable to N (or -1 for all-off).
"""
from __future__ import annotations

from typing import Protocol


class PixelController(Protocol):
    """Minimal interface the mapping loop needs from the Pixelblaze."""

    def pixel_count(self) -> int: ...
    def light(self, pixel: int) -> None: ...
    def all_off(self) -> None: ...
    def close(self) -> None: ...


class PixelblazeController:
    """Real controller backed by pixelblaze-client over ws://<ip>:81.

    pixelblaze-client is imported lazily so the detection/server code (and its
    tests) don't require the dependency unless an actual device is used.
    """

    def __init__(self, ip: str):
        from pixelblaze import Pixelblaze  # lazy import

        self.pb = Pixelblaze(ip)

    def pixel_count(self) -> int:
        return self.pb.getPixelCount()

    def light(self, pixel: int) -> None:
        self.pb.setActiveVariables({"pixel": pixel})

    def all_off(self) -> None:
        self.light(-1)

    def close(self) -> None:
        try:
            self.pb.close()
        except Exception:
            pass


def default_controller_factory(ip: str) -> PixelController:
    """Factory used by the server in production; overridden in tests."""
    return PixelblazeController(ip)
