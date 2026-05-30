"""Automap bridge server.

Serves the static front-end and runs the mapping loop over a WebSocket: the
browser is the camera, this server orchestrates and does the detection, and the
Pixelblaze is driven on the LAN.

WebSocket protocol (text frames are JSON, binary frames are JPEG images):

  client -> server
    {"type": "start", "ip": "<pixelblaze-ip>", "interactive": bool}
    <binary JPEG>                         # sent in reply to a "capture" request
    {"type": "retry"} | {"type": "skip"}  # reply to a "retry_prompt" (interactive)

  server -> client
    {"type": "hello", "pixelCount": N}
    {"type": "capture", "purpose": "background"|"lit", "pixel": n}
    {"type": "retry_prompt", "pixel": n}  # a miss; interactive mode only
    {"type": "progress", "pixel": n, "total": N, "centers": [[x,y], ...]}
    {"type": "done", "map": [[x,y], ...], "missed": k, "center": [cx, cy]}
    {"type": "error", "message": "..."}
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .detection import LedDetector, cluster_center, frame_to_grayscale, map_center
from .pixelblaze_driver import default_controller_factory

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(
    controller_factory=default_controller_factory,
    settle_seconds: float = 0.15,
    confirm_samples: int = 2,
    cleanup_passes: int = 3,
    confirm_tolerance: int = 8,
) -> FastAPI:
    """Build the FastAPI app.

    controller_factory(ip) -> PixelController lets tests inject a fake device.
    settle_seconds: wait after changing the lit pixel before asking for a frame
      (lets the LED and the camera's auto-exposure settle). Default raised to
      give more reliable captures; the client can override it per run.
    confirm_samples: how many agreeing reads are required to accept a position.
    cleanup_passes: how many extra passes to run over the missed LEDs at the end.
    confirm_tolerance: max pixel distance for two reads to count as "agreeing".
    These are defaults; the client may override settle/confirm/cleanup per run.
    """
    app = FastAPI()
    app.state.controller_factory = controller_factory
    app.state.settle_seconds = settle_seconds
    app.state.confirm_samples = confirm_samples
    app.state.cleanup_passes = cleanup_passes
    app.state.confirm_tolerance = confirm_tolerance

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        controller = None
        try:
            start = await websocket.receive_json()
            if start.get("type") != "start":
                await websocket.send_json(
                    {"type": "error", "message": "expected a 'start' message"}
                )
                return

            ip = start.get("ip")
            if not ip:
                await websocket.send_json(
                    {"type": "error", "message": "missing Pixelblaze IP"}
                )
                return

            # Per-run tunables (clamped); fall back to the app defaults.
            def _clamp(v, lo, hi):
                return max(lo, min(hi, v))

            interactive = bool(start.get("interactive", False))
            settle = _clamp(
                float(start.get("settleMs", app.state.settle_seconds * 1000)) / 1000.0,
                0.0, 2.0,
            )
            confirm = int(_clamp(int(start.get("confirm", app.state.confirm_samples)), 1, 5))
            cleanup = int(_clamp(int(start.get("cleanup", app.state.cleanup_passes)), 0, 6))
            tolerance = app.state.confirm_tolerance
            max_reads = confirm + 3  # allow a few failed/disagreeing reads per LED

            # Hardware control is blocking; keep it off the event loop.
            controller = await asyncio.to_thread(app.state.controller_factory, ip)
            count = await asyncio.to_thread(controller.pixel_count)
            await websocket.send_json({"type": "hello", "pixelCount": count})

            detector = LedDetector()
            centers: list = [None] * count  # (x, y) once confirmed, else None
            found = 0

            async def grab(purpose: str, pixel: int):
                await websocket.send_json(
                    {"type": "capture", "purpose": purpose, "pixel": pixel}
                )
                data = await websocket.receive_bytes()
                return frame_to_grayscale(data)

            async def capture_and_detect(pixel: int):
                # Blink the LED (off -> background, on -> lit) and detect.
                await asyncio.to_thread(controller.all_off)
                await asyncio.sleep(settle)
                background = await grab("background", -1)
                await asyncio.to_thread(controller.light, pixel)
                await asyncio.sleep(settle)
                lit = await grab("lit", pixel)
                center, _info = detector.detect_pixel(background, lit)
                return center

            async def attempt_pixel(pixel: int):
                # Read the LED repeatedly; accept once `confirm` reads agree.
                points = []
                for _ in range(max_reads):
                    c = await capture_and_detect(pixel)
                    if c is not None:
                        points.append((c[0], c[1]))
                        agreed = cluster_center(points, tolerance, confirm)
                        if agreed is not None:
                            return agreed
                return None

            def overlay_centers():
                return [list(c) if c is not None else [-1, -1] for c in centers]

            async def send_progress(label: str):
                await websocket.send_json(
                    {
                        "type": "progress",
                        "centers": overlay_centers(),
                        "found": found,
                        "total": count,
                        "label": label,
                    }
                )

            # --- Main pass ---
            for n in range(count):
                center = await attempt_pixel(n)
                if center is None and interactive:
                    while True:  # let the user retry or skip this LED
                        await websocket.send_json({"type": "retry_prompt", "pixel": n})
                        resp = await websocket.receive_json()
                        if resp.get("type") != "retry":
                            break  # skip
                        center = await attempt_pixel(n)
                        if center is not None:
                            break
                if center is not None:
                    centers[n] = center
                    found += 1
                await send_progress(f"Pixel {n + 1} of {count}")

            # --- Cleanup passes: re-try only the missed LEDs ---
            for p in range(cleanup):
                missing = [i for i, c in enumerate(centers) if c is None]
                if not missing:
                    break
                for i in missing:
                    center = await attempt_pixel(i)
                    if center is not None:
                        centers[i] = center
                        found += 1
                    remaining = sum(1 for c in centers if c is None)
                    await send_progress(
                        f"Cleanup pass {p + 1}/{cleanup}: {remaining} missing"
                    )

            await asyncio.to_thread(controller.all_off)

            final = overlay_centers()
            missed = sum(1 for c in centers if c is None)
            cx, cy = map_center(final)
            await websocket.send_json(
                {
                    "type": "done",
                    "map": final,
                    "missed": missed,
                    "center": [cx, cy],
                }
            )
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # surface errors to the UI instead of dying silently
            try:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            # Always turn the LEDs off on exit (normal finish, disconnect, or
            # an aborted/paused-then-stopped run) so none are left stuck on.
            if controller is not None:
                try:
                    await asyncio.to_thread(controller.all_off)
                except Exception:
                    pass
                await asyncio.to_thread(controller.close)

    # Serve the front-end at / (mounted last so /ws takes precedence).
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()


def main():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
