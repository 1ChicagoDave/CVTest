// Browser side of Automap: capture frames from the phone camera and send them
// to the bridge server, which does the detection and drives the Pixelblaze.

const CAP_W = 640, CAP_H = 480; // must match the detector's tuned frame size

const video = document.getElementById("video");
const overlay = document.getElementById("overlay");
const capture = document.getElementById("capture");
const startBtn = document.getElementById("startBtn");
const ipInput = document.getElementById("ip");
const statusEl = document.getElementById("status");
const downloadEl = document.getElementById("download");

const capCtx = capture.getContext("2d", { willReadFrequently: true });
const ovCtx = overlay.getContext("2d");

let ws = null;

function setStatus(msg) { statusEl.textContent = msg; }

// Remember the last IP used.
ipInput.value = localStorage.getItem("pb_ip") || "";

async function enableCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    startBtn.disabled = false;
    setStatus("Camera ready. Enter the Pixelblaze IP and start.");
  } catch (err) {
    setStatus("Camera error: " + err.message + " (HTTPS or localhost required)");
  }
}
// iOS requires a user gesture before camera access.
document.body.addEventListener("click", () => {
  if (!video.srcObject) enableCamera();
}, { once: false });

// Grab the current video frame, downscaled to CAP_W x CAP_H, as a JPEG Blob.
function grabJpeg() {
  capCtx.drawImage(video, 0, 0, CAP_W, CAP_H);
  return new Promise((resolve) =>
    capture.toBlob((b) => resolve(b), "image/jpeg", 0.7)
  );
}

function drawOverlay(centers) {
  // Overlay canvas is sized to its displayed pixels; scale from capture space.
  overlay.width = overlay.clientWidth;
  overlay.height = overlay.clientHeight;
  const sx = overlay.width / CAP_W, sy = overlay.height / CAP_H;
  ovCtx.clearRect(0, 0, overlay.width, overlay.height);
  ovCtx.strokeStyle = "#64ff64";
  ovCtx.lineWidth = 1.5;
  for (const [x, y] of centers) {
    if (x < 0 && y < 0) continue;
    ovCtx.beginPath();
    ovCtx.arc(x * sx, y * sy, 6, 0, Math.PI * 2);
    ovCtx.stroke();
  }
}

function offerDownload(map) {
  const blob = new Blob([JSON.stringify(map)], { type: "application/json" });
  downloadEl.href = URL.createObjectURL(blob);
  downloadEl.style.display = "block";
}

startBtn.addEventListener("click", () => {
  const ip = ipInput.value.trim();
  if (!ip) { setStatus("Enter the Pixelblaze IP first."); return; }
  localStorage.setItem("pb_ip", ip);

  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    startBtn.disabled = true;
    setStatus("Connected. Starting&hellip;");
    ws.send(JSON.stringify({ type: "start", ip }));
  };

  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "hello":
        setStatus(`Mapping ${msg.pixelCount} LEDs&hellip;`);
        break;
      case "capture": {
        const blob = await grabJpeg();
        ws.send(await blob.arrayBuffer());
        break;
      }
      case "progress":
        setStatus(`Pixel ${msg.pixel + 1} of ${msg.total}`);
        drawOverlay(msg.centers);
        break;
      case "done":
        setStatus(`Done. ${msg.map.length} LEDs` +
          (msg.missed ? ` (${msg.missed} not found, marked [-1,-1])` : ""));
        drawOverlay(msg.map);
        offerDownload(msg.map);
        startBtn.disabled = false;
        break;
      case "error":
        setStatus("Error: " + msg.message);
        startBtn.disabled = false;
        break;
    }
  };

  ws.onerror = () => setStatus("WebSocket error.");
  ws.onclose = () => { if (startBtn.disabled) startBtn.disabled = false; };
});
