# Browser-based Automap (bridge server MVP)

Use a phone (or any) browser as the camera for Pixelblaze LED mapping. The
browser captures frames; this Python bridge server reuses CVTest's OpenCV
detection and drives the Pixelblaze. See `../docs/web-automap-vision.md` for the
full design and the Tailscale deployment plan.

This is the **MVP** from that roadmap: prove the full loop on `localhost` (a
secure context, so the camera works) against a real Pixelblaze, before involving
Tailscale and a phone.

## Run it

```bash
cd web
python -m venv .venv && . .venv/bin/activate
pip install --upgrade pip setuptools wheel      # avoids an lzstring build error
pip install -r requirements.txt
python -m automap.server                        # serves on http://localhost:8000
```

Open <http://localhost:8000> on the same machine (localhost counts as a secure
context, so `getUserMedia` works), tap to enable the camera, enter your
Pixelblaze's IP, and press **Start mapping**. The Automap pattern must be running
on the Pixelblaze (see `../Pixelblaze/Automap.epe`). When finished, download
`map.json`.

To use a phone, expose the server over HTTPS — the recommended path is
`tailscale serve https / http://localhost:8000` (see the vision doc).

## How it works

- `automap/detection.py` — camera-independent detection extracted from
  `CVTest.py` (red-channel grayscale + blur, background subtraction, adaptive
  threshold, contour + area/aspect filtering, rotation helper). Frames are tuned
  for 640x480, so the front-end downscales captures to that size.
- `automap/pixelblaze_driver.py` — thin wrapper over `pixelblaze-client`, behind
  a small interface so it can be faked in tests.
- `automap/server.py` — FastAPI app serving the front-end and running the mapping
  loop over a WebSocket (see the protocol docstring at the top of the file).
- `static/` — the browser front-end (camera capture, progress overlay, download).

## Tests

The suite simulates a browser and injects a fake Pixelblaze, so the whole loop
runs with no real camera or hardware:

```bash
pip install -r requirements-dev.txt
pytest
```
