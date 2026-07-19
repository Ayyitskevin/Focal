"""End-to-end smoke for the disposable reviewer-tour process."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parents[1]


def _unused_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_reviewer_tour_serves_then_cleans_up_on_terminate(tmp_path):
    port = _unused_loopback_port()
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "scripts/reviewer_demo.py", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ""
    try:
        with httpx.Client(trust_env=False) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    health = client.get(f"http://127.0.0.1:{port}/healthz", timeout=1)
                    if health.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() >= deadline:
                    raise AssertionError("reviewer tour did not become ready within 15 seconds")
                time.sleep(0.1)

            demo = client.get(f"http://127.0.0.1:{port}/demo", timeout=2)
            pricing = client.get(f"http://127.0.0.1:{port}/pricing", timeout=2)

        assert health.json()["service"] == "mise"
        assert "Commercial content day" in demo.text
        assert "Wedding story collection" in demo.text
        assert "Gallery delivery" in pricing.text
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)

    assert process.returncode == 130, output
    assert "Application startup complete" in output
    assert "Application shutdown complete" in output
    assert not list(tmp_path.glob("mise-review-*"))
