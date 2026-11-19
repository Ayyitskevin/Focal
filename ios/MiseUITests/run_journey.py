"""Run the simulator suite against a disposable loopback Focal studio.

On macOS, install requirements.txt, run xcodegen generate in ios/, then run
python ios/MiseUITests/run_journey.py <available-iPhone-simulator-UDID>.
"""

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
INQUIRY_ID = 73
CLIENT_ID = 402


@contextmanager
def synthetic_studio():
    with tempfile.TemporaryDirectory(prefix="focal-ios-") as data_dir, socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        password = secrets.token_hex(8)
        for key in list(os.environ):
            if key.startswith("MISE_"):
                del os.environ[key]
        os.environ.update(
            MISE_ENV_FILE=str(Path(data_dir) / "no-env-file"),
            MISE_DATA_DIR=data_dir,
            MISE_HOST="127.0.0.1",
            MISE_PORT=str(port),
            MISE_BASE_URL=f"http://127.0.0.1:{port}",
            MISE_ADMIN_PASSWORD=password,
            MISE_SECRET_KEY=secrets.token_hex(32),
            MISE_SHOWCASE_SEED="false",
        )
        sys.path.insert(0, str(ROOT))
        from app import db

        db.migrate()
        address = "portrait@example.test"
        db.run(
            "INSERT INTO clients(id,name,email) VALUES(?,?,?)",
            (CLIENT_ID, "Juniper Portrait Studio", address),
        )
        db.run(
            "INSERT INTO inquiries(id,name,email,message,kind) VALUES(?,?,?,?,?)",
            (INQUIRY_ID, "Autumn portrait request", address, "Portrait session", "contact"),
        )
        # Keep this exact socket bound until shutdown. Concurrent runs get distinct origins.
        server = uvicorn.Server(uvicorn.Config("app.main:app", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("The synthetic Focal studio did not start.")
                time.sleep(0.1)
            yield port, password
        finally:
            server.should_exit = True
            thread.join()


def main(udid: str) -> int:
    with synthetic_studio() as (port, password):
        env = {key: value for key, value in os.environ.items() if not key.startswith("MISE_")}
        env["TEST_RUNNER_FOCAL_JOURNEY_PORT"] = str(port)
        env["TEST_RUNNER_FOCAL_JOURNEY_PASSWORD"] = password
        command = [
            "xcodebuild",
            "test",
            "-project",
            "Mise.xcodeproj",
            "-scheme",
            "FocalSimulator",
            "-destination",
            f"platform=iOS Simulator,id={udid}",
            "CODE_SIGNING_ALLOWED=NO",
        ]
        result = subprocess.run(command, cwd=ROOT / "ios", env=env)
        if result.returncode:
            return result.returncode
        from app import db

        row = db.one("SELECT converted_client_id FROM inquiries WHERE id=?", (INQUIRY_ID,))
        if row["converted_client_id"] != CLIENT_ID:
            raise RuntimeError("The simulator did not convert the inquiry into the linked client.")
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: run_journey.py <iPhone simulator UDID>")
    sys.exit(main(sys.argv[1]))
