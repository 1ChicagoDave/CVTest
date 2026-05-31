# Vision: Browser-Based Automap (use an iPhone camera for LED mapping)

> **Status: BUILT.** This document is the original design. The web tool described here is implemented
> in [`../web/`](../web/) and has been used successfully to map a real strip from an iPhone over Tailscale.
> For how to run it, see [`../web/README.md`](../web/README.md) and [`../web/DEPLOY.md`](../web/DEPLOY.md).
> Two robustness features were added beyond this original design after the first real run: reading each
> LED multiple times and accepting a position only once the reads agree (confirmation), and automatic
> cleanup passes that re-try missed LEDs at the end. The detection itself remains the CVTest algorithm.

## Context — why this is wanted

CVTest today is a desktop Python script. It opens a laptop webcam via OpenCV (`cv2.VideoCapture(0)`),
walks a Pixelblaze through its LEDs one at a time, finds each lit LED in the camera image, and writes a
`map.json` the user imports into the Pixelblaze. Two friction points motivate a browser version:

- The **laptop webcam** is awkward to aim and is usually lower quality than a modern phone camera.
- Setup is fiddly (Python, OpenCV, `pip install`), and `VideoCapture` is slow to initialize on Windows.

The goal: open a web page on an **iPhone in Chrome/Safari**, use the **phone's camera** to do the mapping,
and keep the existing, carefully-tuned detection algorithm. Image processing runs on a **laptop server**
(reusing the current OpenCV code rather than reimplementing it).

**Available infrastructure (provided by the user):**
- **Tailscale** connects all the user's devices (iPhone, laptop) on a private mesh — *except* the Pixelblaze,
  which is only on the local WiFi/LAN. This is the key enabler: **Tailscale Serve can publish the bridge over
  HTTPS with a valid `*.ts.net` certificate automatically**, which is exactly the secure context the iPhone
  camera requires — with no domain, no Let's Encrypt setup, and no port-forwarding.
- A **Linux VM running under Docker on the laptop**, on the same WiFi/LAN as the Pixelblaze and on the tailnet.
  This is the natural home for the bridge server that drives the Pixelblaze.
- A **Hostinger KVM server** (public IP). Not needed for the recommended Tailscale design; kept as an optional
  fallback for accessing the tool when the phone is *off* the tailnet (see "Optional: public access").

## What the hardware actually requires (from reading the current code)

The Pixelblaze side is trivial and already browser-friendly:
- The `Automap.epe` pattern exports a single variable: `pixel` (`render(index){ hsv(0,1, index==pixel) }`).
- Mapping the whole strip is just: read the pixel count, then for each `N`, set `pixel = N`, capture a frame,
  and find the bright blob. The Pixelblaze is controlled over a plain **WebSocket on `ws://<ip>:81`**
  (`setActiveVariables({"pixel": N})`, `getPixelCount()` in `CVTest.py`).

So the only genuinely hard part of "move this to a phone browser" is **getting camera frames into the existing
detection pipeline**, not controlling the hardware.

## The one real obstacle, and how this design removes it

- iOS grants camera access (`getUserMedia`) **only in a secure context (HTTPS)**.
- The Pixelblaze speaks only **`ws://`** (no TLS).
- A browser on an HTTPS page is **blocked from opening a `ws://` connection** to the Pixelblaze (mixed content).

**Resolution:** the phone never talks to the Pixelblaze directly. The browser talks only to *our* bridge server
over secure transport (HTTPS/WSS via **Tailscale Serve**); the **bridge** (on the LAN, server-side, no browser
security rules) talks to the Pixelblaze over plain `ws://`. This also lets us **reuse the existing Python OpenCV
algorithm verbatim** instead of porting it to JavaScript.

## Architecture (recommended: Tailscale, two pieces)

```
 iPhone on tailnet (Chrome/Safari)           Laptop Docker VM: on tailnet AND on Pixelblaze LAN
 ┌───────────────────────────┐               ┌──────────────────────────────────────────────┐
 │  PWA front-end            │   HTTPS/WSS    │  Automap bridge server (Python)              │
 │  • getUserMedia camera    │  over tailnet  │  • served via `tailscale serve` (valid TLS) │
 │  • capture JPEG frames    │ ◀───────────▶  │  • reuses CVTest OpenCV detection           │
 │  • progress + overlay     │  (Tailscale    │  • drives Pixelblaze on the LAN             │
 │  • download map.json      │   Serve cert)  │  • builds map.json                          │
 └───────────────────────────┘               └───────────────────────┬──────────────────────┘
                                                                      │ ws://<pixelblaze-ip>:81 (LAN)
                                                                      ▼
                                                                Pixelblaze + LEDs
```

There is no public relay and no tunnel in the recommended design — Tailscale provides the encrypted path and the
valid certificate, and the iPhone reaches the bridge directly over the tailnet.

1. **Front-end (static PWA), served by the bridge.** Plain HTML/JS that the bridge serves over HTTPS (Tailscale
   Serve terminates TLS with an automatic `*.ts.net` cert, so the iPhone camera works with no warnings).
   Responsibilities: request the camera, show the live preview, capture a frame as JPEG on request, send it over
   a WebSocket, render progress + detected-LED overlay, and finally offer the generated `map.json` as a
   download/share. The user opens the same `https://<vm-host>.<tailnet>.ts.net/` URL each time.

2. **Automap bridge server (Python), in the laptop's Docker VM.** Sits on both the tailnet and the Pixelblaze
   LAN. The existing algorithm lives here, lightly refactored: instead of pulling frames from
   `cv2.VideoCapture`, it receives JPEG frames over the WebSocket and decodes them with `cv2.imdecode`.
   Everything downstream is unchanged — red-channel grayscale, Gaussian blur, background subtraction
   (`cv2.absdiff`), adaptive thresholding, contour detection, area/aspect-ratio filtering, retries. It keeps the
   Pixelblaze control (`pixelblaze-client`) and the final map generation/rotation. `tailscale serve https /` (or
   a sidecar) exposes it on the tailnet.

## Optional: public access (Hostinger fallback)

If the user ever wants to map while the phone is *off* the tailnet, the Hostinger KVM can act as a public WSS
**relay**: the bridge dials *out* to it (no home port-forwarding) and Hostinger pairs the phone session with the
bridge, forwarding messages both ways. The Pixelblaze still never leaves the LAN. This adds a public endpoint
(so it needs a shared token and a domain + Let's Encrypt cert) and is unnecessary whenever the phone is on the
tailnet — hence "optional." `tailscale funnel` is a lighter alternative if brief public exposure is acceptable.

## The mapping control loop (remote-camera handshake)

The current code assumes it can grab a frame whenever it wants. With the camera now remote, the **server
orchestrates** and the phone is a frame source it polls:

1. Server → phone: "send a frame." Pixelblaze is off (`pixel = -1`) → server stores this as the **background**.
2. Server sets `pixel = N`, waits briefly for the LED/display to settle, then → phone: "send a frame" →
   stores as the **lit** frame.
3. Server runs the existing detect-and-threshold logic. On a miss it adjusts the threshold and requests another
   frame (same retry loop as today). On success it records the center and advances `N`.
4. Throughout, server → phone: progress (`pixel N of M`) and the list of found centers, so the phone can draw
   the same green-circle overlay the desktop UI shows.
5. When done, server builds `map.json` and sends it to the phone for download. The "rotate with a/d, space to
   save" step becomes on-screen buttons/drag on the phone.

The **camera-must-stay-still** requirement from the README still applies (prop the phone up) — removing it is
the separate ML/"phone app" direction the author mentions, and is explicitly out of scope here.

## Reuse vs. new

**Reused from `CVTest.py` (the valuable, tuned part):** `estimate_threshold`, the `absdiff`/`threshold`/
`findContours` pipeline and its area + aspect-ratio acceptance logic and retry/threshold-adjust loop, the
red-channel grayscale + Gaussian blur in `get_frame`, background subtraction, `rotate_led_centers`, center
computation, the `[-1,-1]` "couldn't find it" tagging, and the Pixelblaze control (`start_pixelblaze`,
`light_pixel`, `all_pixels_off`, `getPixelCount`).

**New / changed:**
- Replace `cv2.VideoCapture` + `get_frame`'s camera grab with "decode the JPEG the phone just sent"
  (`cv2.imdecode`). The frame-skip/ring-buffer logic (`framesToSkip`) becomes unnecessary.
- Replace the OpenCV `imshow` windows and `cv2.waitKey` key handling with WebSocket messages to the phone UI.
- Add a small async WebSocket server (e.g., `websockets`/`aiohttp` or FastAPI) wrapping the loop, also serving
  the static front-end.
- New static front-end (HTML/JS PWA), exposed to the iPhone via `tailscale serve`.

## Suggested tech choices

- **Front-end:** vanilla JS + Canvas (no framework needed). `getUserMedia({ video: { facingMode: 'environment' }})`,
  `canvas.toBlob('image/jpeg', ~0.7)` per requested frame. Optionally a PWA manifest so it installs to the home screen.
- **Bridge server (laptop Docker VM):** Python with FastAPI (or `websockets`) serving both the static PWA and
  the WebSocket, reusing the current OpenCV/pixelblaze-client deps already in `requirements.txt`. Containerize it
  to run in the existing Docker VM.
- **Secure exposure:** `tailscale serve https / http://localhost:<port>` puts the bridge on the tailnet with an
  automatic valid cert — no reverse proxy, domain, or Let's Encrypt management needed.
- **Optional public relay (Hostinger KVM):** only if off-tailnet access is wanted — a small FastAPI/`websockets`
  relay behind Caddy (auto Let's Encrypt). Not part of the recommended build.

## Phased roadmap (for an eventual build)

1. **Bridge server MVP** — refactor the CVTest loop to take frames over a WebSocket and drive the Pixelblaze;
   prove it end-to-end with a trivial test page on the laptop (`localhost` is a secure context, so the camera
   works there first).
2. **Front-end PWA** — camera capture, frame-on-request, progress + overlay, map download.
3. **Expose over Tailscale** — `tailscale serve` the bridge and open it from the iPhone over the tailnet.
4. **UX polish** — on-screen aim/start, rotate-to-orient, retry/skip controls, reconnect handling.
5. **(Future, out of scope)** — optional Hostinger relay for off-tailnet use; move detection into the browser
   (OpenCV.js) to drop the laptop entirely; the ML approach that removes the camera-still constraint.

## Risks / open questions

- **Frame transport latency** — frames travel phone → bridge over the tailnet (direct, usually low latency, but
  relayed via DERP if NAT traversal fails). JPEG quality/size and "settle" timing are the tuning knobs; mapping
  may still be slower than the (already slow) desktop version.
- **Docker networking on the laptop VM must reach the Pixelblaze AND join the tailnet.** The container needs LAN
  access to `ws://<pixelblaze-ip>:81` (likely `--network host` or macvlan/bridged, not default NAT) while also
  being reachable on the tailnet — confirm both with a smoke test (WebSocket to the Pixelblaze + `tailscale
  status`) early. Running `tailscaled` inside the container vs. serving from the VM host is a setup decision.
- **Tailscale Serve specifics** — confirm MagicDNS/HTTPS certs are enabled for the tailnet, and that Serve
  proxies WebSocket upgrades (it does) for the frame channel.
- **White balance / exposure** on phones is more aggressive than webcams; background subtraction handles much of
  this, but auto-exposure lock may be worth requesting via camera constraints.
- **Optional Hostinger relay** (only if off-tailnet access is added) is a public endpoint — it would need a
  shared token and a domain + Let's Encrypt cert, and must not expose the Pixelblaze beyond relaying the session.

## Verification (how an eventual build would be proven)

- **Local first:** run the bridge server and open the test page on `http://localhost` on the laptop (a secure
  context) to validate the full loop against a real Pixelblaze before involving the phone.
- **Phone end-to-end:** `tailscale serve` the bridge, open `https://<vm-host>.<tailnet>.ts.net/` on the iPhone,
  point it at a real LED setup, run a full map, and confirm the downloaded `map.json` matches the desktop tool's
  output for the same rig.
- **Regression:** keep `CVTest.py` working unchanged so results from the browser path can be diffed against the
  desktop path on the same LEDs.
