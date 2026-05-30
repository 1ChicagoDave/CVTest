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

from .detection import LedDetector, frame_to_grayscale, map_center
from .pixelblaze_driver import default_controller_factory

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(controller_factory=default_controller_factory, settle_seconds: float = 0.08) -> FastAPI:
    """Build the FastAPI app.

    controller_factory(ip) -> PixelController lets tests inject a fake device.
    settle_seconds is how long to wait after changing the lit pixel before
    asking the phone for a frame (lets the LED/display and auto-exposure settle).
    """
    app = FastAPI()
    app.state.controller_factory = controller_factory
    app.state.settle_seconds = settle_seconds

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

            # When interactive, pause on a miss and let the user retry or skip;
            # otherwise auto-skip (tag [-1,-1]) like the desktop tool.
            interactive = bool(start.get("interactive", False))

            # Hardware control is blocking; keep it off the event loop.
            controller = await asyncio.to_thread(app.state.controller_factory, ip)
            count = await asyncio.to_thread(controller.pixel_count)
            await websocket.send_json({"type": "hello", "pixelCount": count})

            detector = LedDetector()
            centers: list[list[int]] = []
            missed = 0

            async def grab(purpose: str, pixel: int):
                await websocket.send_json(
                    {"type": "capture", "purpose": purpose, "pixel": pixel}
                )
                data = await websocket.receive_bytes()
                return frame_to_grayscale(data)

            async def capture_and_detect(pixel: int):
                # Background frame with everything off, then the lit LED.
                await asyncio.to_thread(controller.all_off)
                await asyncio.sleep(app.state.settle_seconds)
                background = await grab("background", -1)
                await asyncio.to_thread(controller.light, pixel)
                await asyncio.sleep(app.state.settle_seconds)
                lit = await grab("lit", pixel)
                center, _info = detector.detect_pixel(background, lit)
                return center

            for n in range(count):
                while True:
                    center = await capture_and_detect(n)
                    if center is not None:
                        centers.append([int(center[0]), int(center[1])])
                        break

                    # Miss. In interactive mode, ask the user what to do.
                    if interactive:
                        await websocket.send_json({"type": "retry_prompt", "pixel": n})
                        resp = await websocket.receive_json()
                        if resp.get("type") == "retry":
                            continue  # re-light, re-capture, re-detect
                    centers.append([-1, -1])
                    missed += 1
                    break

                await websocket.send_json(
                    {
                        "type": "progress",
                        "pixel": n,
                        "total": count,
                        "centers": centers,
                    }
                )

            await asyncio.to_thread(controller.all_off)

            cx, cy = map_center(centers)
            await websocket.send_json(
                {
                    "type": "done",
                    "map": centers,
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
            if controller is not None:
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
