# Browser-based Automap

Map Pixelblaze LED positions using a **phone (or laptop) browser camera**. The
browser is the camera; a small Python bridge server runs the OpenCV detection
(reused from the desktop `CVTest.py`) and drives the Pixelblaze; the page is
reached securely over Tailscale so the iPhone camera works with no certificate
warnings. See `../docs/web-automap-vision.md` for the full design.

The result is a `map.json` you import into the Pixelblaze mapping tab — the same
output as the desktop tool, but captured with a phone you can prop up anywhere.

## Quick start (laptop / localhost)

`localhost` is a secure context, so the camera works without HTTPS — this is the
easiest way to try it before involving a phone.

```bash
cd web
python3 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip setuptools wheel   # avoids an lzstring build error
pip install -r requirements-server.txt       # headless OpenCV; works on WSL/servers
python -m automap.server                     # serves on http://localhost:8000
```

> Use `requirements-server.txt` (it installs `opencv-python-headless`). The plain
> `requirements.txt` pulls the desktop OpenCV build, which needs system graphics
> libraries (`libGL`) that headless WSL/Linux boxes usually lack.

Open <http://localhost:8000>, tap to enable the camera, enter your Pixelblaze's
IP, set the options (below), and press **Start mapping**. The **Automap** pattern
must be the running pattern on the Pixelblaze, with the sequencer off
(see `../Pixelblaze/Automap.epe`). When finished, orient and download `map.json`.

## Use it from an iPhone (Tailscale)

Expose the running server to your tailnet and open it on the phone — full steps
in **[DEPLOY.md](DEPLOY.md)**. Short version, from the machine where Tailscale
runs:

```bash
tailscale serve --bg http://localhost:8000
tailscale serve status   # prints https://<machine>.<tailnet>.ts.net/
```

Open that `https://…ts.net/` URL on the iPhone (on the same tailnet). It installs
to the home screen as a full-screen app via Share → Add to Home Screen.

## Mapping options

Set on the start screen and sent with the run:

- **Settle (ms)** — wait after lighting each LED before capturing, so the LED and
  the camera's auto-exposure settle. Higher = slower but more reliable. Default
  150; try 250–300 if you see misses.
- **Confirm reads** — each LED is blinked and read repeatedly, and a position is
  accepted only once this many reads **agree** within a few pixels (then
  averaged). This rejects noise and improves accuracy. Default 2.
  (Confirm = 1 disables the agreement check and is more prone to false hits.)
- **Cleanup passes** — after the main pass, re-try **only the missed LEDs** this
  many times, filling in any now-findable ones. Default 3.
- **Pause on each miss** — optional manual retry/skip per miss. Off by default,
  since cleanup passes handle misses automatically.

After mapping, rotate the map (buttons or slider) to match the physical
orientation of your LEDs, then download. LEDs that still can't be found are
tagged `[-1, -1]` so you can hand-edit them.

During a run you can **Stop/Pause** → then **Resume** or **Restart** (Restart
returns to the start screen without auto-starting). Stopping always turns the
LEDs back off.

## How it works

- `automap/detection.py` — camera-independent detection extracted from
  `CVTest.py` (red-channel grayscale + blur, background subtraction, adaptive
  threshold, contour + area/aspect filtering), plus `cluster_center()` (the
  confirm-reads agreement check) and the rotation helper. Tuned for 640×480, so
  the front-end center-crops captures to that size.
- `automap/pixelblaze_driver.py` — thin wrapper over `pixelblaze-client`, behind
  a small interface so it can be faked in tests.
- `automap/server.py` — FastAPI app serving the front-end and running the mapping
  loop (main pass + cleanup passes) over a WebSocket. The protocol is documented
  in the docstring at the top of the file.
- `static/` — the browser front-end (camera capture, live overlay, options,
  pause/resume, rotate-to-orient, download) and the PWA manifest/icons.

## Tests

The suite simulates a browser and injects a fake Pixelblaze, so the whole loop —
including confirm reads, cleanup recovery, and the interactive retry/skip path —
runs with no real camera or hardware:

```bash
pip install -r requirements-dev.txt
pytest
```
