# Deploying the Automap bridge (Docker VM + Tailscale)

This is phase 3 of the [vision](../docs/web-automap-vision.md): run the bridge in
the laptop's Docker VM and expose it to your iPhone over the tailnet, with a
valid HTTPS cert (which is what the phone camera requires) via `tailscale serve`.

```
 iPhone (on tailnet) ──HTTPS/WSS──▶ tailscale serve ──▶ bridge :8000 (Docker, host net) ──ws://──▶ Pixelblaze
```

## Prerequisites

- The **Docker VM** is on the same LAN as the Pixelblaze **and** joined to your
  tailnet (`tailscale status` lists it).
- **MagicDNS + HTTPS certificates** are enabled for the tailnet (Tailscale admin
  console → DNS → enable MagicDNS and "HTTPS Certificates"). `tailscale serve`
  needs this to mint the `*.ts.net` cert.
- Your **iPhone is on the tailnet** (Tailscale app, logged into the same account).
- The Pixelblaze is running the **Automap** pattern (`../Pixelblaze/Automap.epe`).

## 1. Run the bridge container

From the repo's `web/` directory inside the VM:

```bash
docker compose up -d --build
```

`network_mode: host` (see `docker-compose.yml`) means the container shares the
VM's network: it can reach `ws://<pixelblaze-ip>:81` on the LAN, and it listens
on the VM's own `:8000`.

Verify it's up locally on the VM:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/   # expect 200
```

## 2. Publish it on the tailnet with Tailscale Serve

`tailscale serve` runs on the **VM host** (not inside the container) and
terminates HTTPS with an automatic cert, proxying to the bridge on `:8000`.
WebSocket upgrades (the `/ws` frame channel) pass through transparently.

```bash
sudo tailscale serve --bg http://localhost:8000
tailscale serve status        # shows your https://<vm-host>.<tailnet>.ts.net/ URL
```

> Older Tailscale CLIs use `sudo tailscale serve https / http://localhost:8000`.
> If `--bg` isn't recognized, run `tailscale serve --help` for your version.

## 3. Map from the iPhone

1. On the iPhone (on the tailnet), open `https://<vm-host>.<tailnet>.ts.net/`.
2. Tap the page to enable the camera (iOS needs a user gesture); the valid cert
   means no certificate warnings.
3. Prop the phone so it sees all the LEDs, enter the Pixelblaze IP, and tap
   **Start mapping**.
4. When it finishes, download `map.json` and import it into the Pixelblaze
   mapping tab.

## Stopping / cleanup

```bash
tailscale serve reset            # stop publishing
docker compose down              # stop the bridge
```

## Notes & troubleshooting

- **Can't reach the Pixelblaze from the container?** Confirm host networking is
  active (`docker inspect` shows `"NetworkMode": "host"`) and that
  `ws://<pixelblaze-ip>:81` is reachable from the VM itself.
- **`tailscaled` location.** Running it on the VM host (recommended) keeps
  networking simple. Running Tailscale *inside* the container is possible but
  needs `--cap-add=NET_ADMIN` and a `tailscaled` sidecar — unnecessary here.
- **Off-tailnet access** (phone not on the tailnet) is the optional Hostinger
  relay path in the vision doc, or `tailscale funnel` for brief public exposure.
- **Rebuild after code changes:** `docker compose up -d --build`.
