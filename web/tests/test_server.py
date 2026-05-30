"""End-to-end test of the bridge server's mapping loop.

Simulates the browser (responding to capture requests with synthetic JPEG
frames) and injects a fake Pixelblaze, so the full WebSocket protocol and
orchestration run without any real camera or hardware.
"""
from fastapi.testclient import TestClient

from automap.server import create_app
from .helpers import FakePixelblaze, make_jpeg


# Where the "lit" LED appears for pixel n, in 640x480 capture space.
def expected_xy(n):
    return (120 + n * 80, 240)


def build_client(count):
    fake = FakePixelblaze(count)
    app = create_app(controller_factory=lambda ip: fake, settle_seconds=0.0)
    return TestClient(app), fake


def run_mapping(ws, count):
    """Drive the protocol from the browser's side; return the final map."""
    ws.send_json({"type": "start", "ip": "192.168.1.42"})
    final = None
    progress_count = 0
    while True:
        msg = ws.receive_json()
        t = msg["type"]
        if t == "hello":
            assert msg["pixelCount"] == count
        elif t == "capture":
            if msg["purpose"] == "background":
                ws.send_bytes(make_jpeg(led_xy=None, noise=3))
            else:  # lit
                ws.send_bytes(make_jpeg(led_xy=expected_xy(msg["pixel"]), noise=3))
        elif t == "progress":
            progress_count += 1
        elif t == "done":
            final = msg
            break
        elif t == "error":
            raise AssertionError("server error: " + msg["message"])
    assert progress_count == count
    return final


def test_full_map_matches_expected_positions():
    count = 4
    client, fake = build_client(count)
    with client.websocket_connect("/ws") as ws:
        done = run_mapping(ws, count)

    assert done["missed"] == 0
    assert len(done["map"]) == count
    for n, (x, y) in enumerate(done["map"]):
        ex, ey = expected_xy(n)
        assert abs(x - ex) <= 5, f"pixel {n}: x {x} vs {ex}"
        assert abs(y - ey) <= 5, f"pixel {n}: y {y} vs {ey}"

    # Pixelblaze was driven: each pixel lit, and turned off at the end.
    assert fake.current == -1
    for n in range(count):
        assert n in fake.history


def test_missing_led_is_tagged():
    count = 2
    client, fake = build_client(count)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "start", "ip": "10.0.0.1"})
        done = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "capture":
                if msg["purpose"] == "lit" and msg["pixel"] == 1:
                    ws.send_bytes(make_jpeg(led_xy=None))  # LED 1 never lights
                elif msg["purpose"] == "lit":
                    ws.send_bytes(make_jpeg(led_xy=expected_xy(msg["pixel"])))
                else:
                    ws.send_bytes(make_jpeg(led_xy=None))
            elif msg["type"] == "done":
                done = msg
                break
    assert done["missed"] == 1
    assert done["map"][1] == [-1, -1]


def test_bad_start_message_errors():
    client, _ = build_client(1)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "nope"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_interactive_skip_tags_missing():
    """In interactive mode, a miss prompts; 'skip' tags [-1,-1] and advances."""
    client, _ = build_client(1)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "start", "ip": "1.2.3.4", "interactive": True})
        done = None
        prompted = False
        while True:
            msg = ws.receive_json()
            if msg["type"] == "capture":
                ws.send_bytes(make_jpeg(led_xy=None))  # never lights -> miss
            elif msg["type"] == "retry_prompt":
                prompted = True
                ws.send_json({"type": "skip"})
            elif msg["type"] == "done":
                done = msg
                break
        assert prompted
        assert done["map"] == [[-1, -1]]
        assert done["missed"] == 1


def test_interactive_retry_then_success():
    """First attempt misses and prompts; after 'retry', a good frame is found."""
    client, _ = build_client(1)
    attempts = {"lit": 0}
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "start", "ip": "1.2.3.4", "interactive": True})
        done = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "capture":
                if msg["purpose"] == "background":
                    ws.send_bytes(make_jpeg(led_xy=None))
                else:
                    attempts["lit"] += 1
                    # Miss on the first lit capture, succeed on the retry.
                    if attempts["lit"] == 1:
                        ws.send_bytes(make_jpeg(led_xy=None))
                    else:
                        ws.send_bytes(make_jpeg(led_xy=(300, 200)))
            elif msg["type"] == "retry_prompt":
                ws.send_json({"type": "retry"})
            elif msg["type"] == "done":
                done = msg
                break
        assert done["missed"] == 0
        x, y = done["map"][0]
        assert abs(x - 300) <= 5 and abs(y - 200) <= 5
        assert attempts["lit"] >= 2  # proves the retry actually re-captured
