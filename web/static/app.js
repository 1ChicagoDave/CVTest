// Browser side of Automap: capture frames from the phone camera, send them to
// the bridge server (which detects + drives the Pixelblaze), then let the user
// orient and save the resulting map. Includes retry/skip and reconnect handling.

const CAP_W = 640, CAP_H = 480; // must match the detector's tuned frame size
const ROT_STEP = 0.0628;        // ~3.6deg per tap, same as the desktop a/d keys

const $ = (id) => document.getElementById(id);
const video = $("video"), overlay = $("overlay"), capture = $("capture");
const statusEl = $("status"), barFill = $("bar").firstElementChild;
const ipInput = $("ip"), interactiveBox = $("interactive");
const settleInput = $("settle"), confirmInput = $("confirm"), cleanupInput = $("cleanup");

// Parse a number input, falling back to a default and clamping to a range.
function numInput(el, def, lo, hi) {
  const v = parseInt(el.value, 10);
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : def;
}

const capCtx = capture.getContext("2d", { willReadFrequently: true });
const ovCtx = overlay.getContext("2d");

let ws = null;
let runState = "idle";          // idle | mapping | done
let pendingStart = null;        // the {start...} message, for reconnect resume
let reconnectAttempts = 0;

// Pause state. When paused we simply withhold frames; the server waits on the
// next frame, so the run freezes until we send again (resume) or disconnect
// (restart).
let paused = false;
let captureHeld = false;        // a capture was requested while paused

// Orientation state (set when mapping finishes).
let rawMap = [], mapCenter = [CAP_W / 2, CAP_H / 2], theta = 0;

function setStatus(html) { statusEl.innerHTML = html; }
function setProgress(done, total) {
  barFill.style.width = total ? `${(done / total) * 100}%` : "0";
}
function showPanel(name) {
  for (const p of ["aimPanel", "mappingPanel", "retryPanel", "orientPanel"]) {
    $(p).classList.toggle("active", p === name + "Panel");
  }
}
function resetPauseUI() {
  paused = false;
  captureHeld = false;
  $("pausedRow").style.display = "none";
  $("pauseBtn").style.display = "";
}

// ---- Camera ----------------------------------------------------------------
async function enableCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: "environment",
        aspectRatio: { ideal: 4 / 3 },
        width: { ideal: 1280 }, height: { ideal: 960 },
      },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    setStatus("Camera ready. Aim it, enter the Pixelblaze IP, then Start.");
    showPanel("aim");
  } catch (err) {
    setStatus("Camera error: " + err.message + " (HTTPS or localhost required)");
  }
}
document.body.addEventListener("click", () => {
  if (!video.srcObject) enableCamera();
});

// Grab the current video frame, center-cropped to 4:3 (no stretching) and
// scaled to CAP_W x CAP_H. Cropping to 4:3 matches the video's object-fit:cover
// so the dots line up with what's on screen, and avoids distorting the map.
function grabJpeg() {
  const vw = video.videoWidth || CAP_W, vh = video.videoHeight || CAP_H;
  const targetAR = CAP_W / CAP_H;
  let sw, sh, sx0, sy0;
  if (vw / vh > targetAR) {        // wider than 4:3 -> crop the sides
    sh = vh; sw = vh * targetAR; sx0 = (vw - sw) / 2; sy0 = 0;
  } else {                         // taller than 4:3 -> crop top/bottom
    sw = vw; sh = vw / targetAR; sx0 = 0; sy0 = (vh - sh) / 2;
  }
  capCtx.drawImage(video, sx0, sy0, sw, sh, 0, 0, CAP_W, CAP_H);
  return new Promise((resolve) => capture.toBlob((b) => resolve(b), "image/jpeg", 0.7));
}

// Grab a frame and send it, in reply to a server "capture" request.
async function sendFrame() {
  const blob = await grabJpeg();
  if (ws && ws.readyState === 1) ws.send(await blob.arrayBuffer());
}

// ---- Overlay ---------------------------------------------------------------
// The overlay canvas bitmap is a fixed CAP_W x CAP_H and CSS scales it to fit
// the 4:3 stage, so centers (in capture coordinates) can be drawn directly.
function drawOverlay(centers, withCrosshair) {
  ovCtx.clearRect(0, 0, CAP_W, CAP_H);
  if (withCrosshair) {
    ovCtx.strokeStyle = "#c83232";
    ovCtx.lineWidth = 2;
    const cx = mapCenter[0], cy = mapCenter[1];
    ovCtx.beginPath();
    ovCtx.moveTo(cx - 40, cy); ovCtx.lineTo(cx + 40, cy);
    ovCtx.moveTo(cx, cy - 40); ovCtx.lineTo(cx, cy + 40);
    ovCtx.stroke();
  }
  ovCtx.strokeStyle = "#64ff64";
  ovCtx.lineWidth = 2;
  for (const [x, y] of centers) {
    if (x < 0 && y < 0) continue;
    ovCtx.beginPath();
    ovCtx.arc(x, y, 7, 0, Math.PI * 2);
    ovCtx.stroke();
  }
}

// ---- Rotation (mirrors detection.rotate_led_centers) -----------------------
function rotateCenters(centers, cx, cy, rad) {
  return centers.map(([x, y]) => {
    if (x < 0 && y < 0) return [-1, -1];
    const dx = x - cx, dy = y - cy;
    return [
      Math.round(dx * Math.cos(rad) - dy * Math.sin(rad) + cx),
      Math.round(dx * Math.sin(rad) + dy * Math.cos(rad) + cy),
    ];
  });
}

function refreshOriented() {
  const oriented = rotateCenters(rawMap, mapCenter[0], mapCenter[1], theta);
  drawOverlay(oriented, true);
  const blob = new Blob([JSON.stringify(oriented)], { type: "application/json" });
  $("download").href = URL.createObjectURL(blob);
}

// ---- WebSocket / mapping ---------------------------------------------------
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function connect() {
  ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    reconnectAttempts = 0;
    if (pendingStart) ws.send(JSON.stringify(pendingStart));
  };

  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "hello":
        setStatus(`Mapping ${msg.pixelCount} LEDs&hellip;`);
        setProgress(0, msg.pixelCount);
        showPanel("mapping");
        break;
      case "capture":
        if (paused) { captureHeld = true; }   // hold until resume
        else { await sendFrame(); }
        break;
      case "retry_prompt":
        $("retryMsg").textContent = `Couldn't find LED ${msg.pixel + 1}.`;
        showPanel("retry");
        break;
      case "progress":
        setStatus(msg.label);
        setProgress(msg.found, msg.total);
        drawOverlay(msg.centers, false);
        if (!paused) showPanel("mapping"); // back from a retry prompt
        break;
      case "done":
        runState = "done";
        pendingStart = null;
        resetPauseUI();
        rawMap = msg.map;
        mapCenter = msg.center || [CAP_W / 2, CAP_H / 2];
        theta = 0;
        $("rotSlider").value = 0;
        setStatus(`Done. ${msg.map.length} LEDs` +
          (msg.missed ? ` (${msg.missed} not found, marked [-1,-1])` : "") +
          ". Rotate to orient, then download.");
        showPanel("orient");
        refreshOriented();
        break;
      case "error":
        runState = "idle";
        pendingStart = null;
        resetPauseUI();
        setStatus("Error: " + msg.message);
        showPanel("aim");
        break;
    }
  };

  ws.onclose = () => {
    if (runState === "mapping") scheduleReconnect();
  };
  ws.onerror = () => { /* onclose will handle reconnect */ };
}

function scheduleReconnect() {
  if (reconnectAttempts >= 5) {
    runState = "idle";
    pendingStart = null;
    setStatus("Disconnected. Tap Start to try again.");
    showPanel("aim");
    return;
  }
  const delay = Math.min(1000 * 2 ** reconnectAttempts, 8000);
  reconnectAttempts++;
  // A reconnect restarts the run from scratch, so clear any pause state;
  // otherwise the reconnected run would silently withhold its first frame.
  resetPauseUI();
  setStatus(`Disconnected &mdash; reconnecting (attempt ${reconnectAttempts}, restarts the map)&hellip;`);
  setTimeout(connect, delay);
}

// ---- Controls --------------------------------------------------------------
ipInput.value = localStorage.getItem("pb_ip") || "";

$("startBtn").addEventListener("click", () => {
  const ip = ipInput.value.trim();
  if (!ip) { setStatus("Enter the Pixelblaze IP first."); return; }
  localStorage.setItem("pb_ip", ip);
  pendingStart = {
    type: "start", ip,
    interactive: interactiveBox.checked,
    settleMs: numInput(settleInput, 150, 0, 2000),
    confirm: numInput(confirmInput, 2, 1, 5),
    cleanup: numInput(cleanupInput, 3, 0, 6),
  };
  runState = "mapping";
  reconnectAttempts = 0;
  resetPauseUI();
  setStatus("Connecting&hellip;");
  connect();
});

$("retryBtn").addEventListener("click", () => {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "retry" }));
  setStatus("Retrying&hellip;");
  showPanel("mapping");
});
$("skipBtn").addEventListener("click", () => {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "skip" }));
  setStatus("Skipped.");
  showPanel("mapping");
});

// Pause: withhold frames so the run freezes. Splits the wide button into
// Resume / Restart.
$("pauseBtn").addEventListener("click", () => {
  paused = true;
  $("pauseBtn").style.display = "none";
  $("pausedRow").style.display = "";
  setStatus("Paused. Adjust if needed, then Resume or Restart.");
});
$("resumeBtn").addEventListener("click", () => {
  paused = false;
  $("pausedRow").style.display = "none";
  $("pauseBtn").style.display = "";
  setStatus("Resuming&hellip;");
  if (captureHeld) { captureHeld = false; sendFrame(); } // answer the held request
});
// Restart: abort this run and go back to the aim screen (does NOT auto-start).
$("abortBtn").addEventListener("click", () => {
  runState = "idle";          // prevents auto-reconnect on close
  pendingStart = null;
  resetPauseUI();
  if (ws) { try { ws.close(); } catch (e) {} }
  setStatus("Mapping stopped. Aim the camera, then Start mapping.");
  showPanel("aim");
});

$("rotLeft").addEventListener("click", () => {
  theta -= ROT_STEP;
  $("rotSlider").value = Math.round((theta * 180) / Math.PI);
  refreshOriented();
});
$("rotRight").addEventListener("click", () => {
  theta += ROT_STEP;
  $("rotSlider").value = Math.round((theta * 180) / Math.PI);
  refreshOriented();
});
$("rotSlider").addEventListener("input", (e) => {
  theta = (Number(e.target.value) * Math.PI) / 180;
  refreshOriented();
});
$("restartBtn").addEventListener("click", () => {
  runState = "idle";
  setStatus("Aim the camera, then Start mapping.");
  showPanel("aim");
});
