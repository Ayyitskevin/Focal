"""Run the simulator suite against a disposable loopback Focal studio.

On macOS, install requirements.txt, run xcodegen generate in ios/, then run
python ios/MiseUITests/run_journey.py <available-iPhone-simulator-UDID>.
That device supplies the hardware type and runtime for a new, empty simulator.
"""

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
INQUIRY_ID = 73
CLIENT_ID = 402
SERVICE = "Orchard portraits"
SHOOT_DATE = "2030-10-13"
PROJECT_TITLE = f"{SERVICE} — {SHOOT_DATE}"


@contextmanager
def empty_iphone(reference_udid: str):
    simctl = ["xcrun", "simctl"]
    inventory = json.loads(subprocess.check_output([*simctl, "list", "devices", "--json"]))
    devices = {
        device["udid"].upper(): (runtime, device)
        for runtime, entries in inventory["devices"].items()
        for device in entries
    }
    reference = devices.get(reference_udid.upper())
    if reference is None:
        raise ValueError(f"Unknown iPhone simulator {reference_udid}")
    runtime, device = reference
    model = device.get("deviceTypeIdentifier", "")
    if not device.get("isAvailable") or not model.startswith(
        "com.apple.CoreSimulator.SimDeviceType.iPhone-"
    ):
        raise ValueError(f"An available iPhone simulator is required: {reference_udid}")
    name = f"Focal journey {secrets.token_hex(6)}"
    created = subprocess.check_output([*simctl, "create", name, model, runtime], text=True)
    udid = str(UUID(created.strip())).upper()
    if udid in devices:
        raise RuntimeError(f"simctl create returned an existing simulator: {udid}")
    try:
        print(f"Testing on new simulator {udid} ({model}, {runtime}).", flush=True)
        yield udid
    finally:
        subprocess.run([*simctl, "delete", udid], check=True)


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
        # A lookup using the inquiry ID would return this other project for the same client.
        db.run(
            "INSERT INTO projects(id,client_id,title) VALUES(?,?,?)",
            (INQUIRY_ID, CLIENT_ID, "Previous headshots"),
        )
        db.run(
            """INSERT INTO inquiries(id,name,email,message,kind,service,shoot_date)
               VALUES(?,?,?,?,'booking',?,?)""",
            (
                INQUIRY_ID,
                "Autumn portrait request",
                address,
                "Portrait session",
                SERVICE,
                SHOOT_DATE,
            ),
        )
        db.run(
            "INSERT INTO inquiries(name,email,message,kind) VALUES(?,?,?,'contact')",
            ("Winter portrait request", address, "Different inquiry from the same client"),
        )
        from app.main import app

        project_reads: set[str] = set()

        @app.middleware("http")
        async def record_project_read(request, call_next):
            response = await call_next(request)
            path = request.url.path
            if (
                request.method == "GET"
                and response.status_code == 200
                and path.startswith("/api/v1/projects/")
            ):
                project_reads.add(path)
            return response

        # Keep this exact socket bound until shutdown. Concurrent runs get distinct origins.
        server = uvicorn.Server(uvicorn.Config(app, access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("The synthetic Focal studio did not start.")
                time.sleep(0.1)
            yield port, password, project_reads
        finally:
            server.should_exit = True
            thread.join()


def main(reference_udid: str) -> int:
    with (
        empty_iphone(reference_udid) as udid,
        synthetic_studio() as (port, password, project_reads),
    ):
        env = {key: value for key, value in os.environ.items() if not key.startswith("MISE_")}
        env["TEST_RUNNER_FOCAL_JOURNEY_PORT"] = str(port)
        env["TEST_RUNNER_FOCAL_JOURNEY_PASSWORD"] = password
        env["TEST_RUNNER_FOCAL_JOURNEY_PROJECT_TITLE"] = PROJECT_TITLE
        command = [
            "xcodebuild",
            "test",
            "-project",
            "Mise.xcodeproj",
            "-scheme",
            "FocalSimulator",
            "-destination",
            f"platform=iOS Simulator,id={udid}",
            "-parallel-testing-enabled",
            "NO",
            # The signed simulator app uses its own Keychain for the real owner login.
            "CODE_SIGN_IDENTITY=-",
        ]
        result = subprocess.run(command, cwd=ROOT / "ios", env=env)
        if result.returncode:
            return result.returncode
        from app import db

        row = db.one(
            """SELECT i.converted_client_id, p.id AS project_id, p.title
                 FROM inquiries i JOIN projects p
                   ON p.id=i.converted_project_id AND p.client_id=i.converted_client_id
                WHERE i.id=?""",
            (INQUIRY_ID,),
        )
        if row is None or row["converted_client_id"] != CLIENT_ID or row["title"] != PROJECT_TITLE:
            raise RuntimeError(
                "The simulator did not convert the booking into the expected records."
            )
        if project_reads != {f"/api/v1/projects/{row['project_id']}"}:
            raise RuntimeError(f"The simulator did not read the linked project: {project_reads!r}")
        print(f"Verified inquiry {INQUIRY_ID} opened its stored project {row['project_id']}.")
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: run_journey.py <iPhone simulator UDID>")
    # Unwind the fixture and simulator contexts when a CI job is cancelled.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    sys.exit(main(sys.argv[1]))
