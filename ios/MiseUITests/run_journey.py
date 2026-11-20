"""Run the simulator suite against a disposable loopback Focal studio.

On macOS, install requirements.txt, run xcodegen generate in ios/, then run
python ios/MiseUITests/run_journey.py <available-iPhone-simulator-UDID>.
That device supplies the hardware type and runtime for a new, empty simulator.
"""

import asyncio
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
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, build_opener
from uuid import UUID

import uvicorn
from starlette.responses import Response

ROOT = Path(__file__).resolve().parents[2]
INQUIRY_ID = 73
CLIENT_ID = 402
SERVICE = "Orchard portraits"
SHOOT_DATE = "2030-10-13"
PROJECT_TITLE = f"{SERVICE} — {SHOOT_DATE}"
WEB_CONVERT = f"/admin/studio/inquiries/{INQUIRY_ID}/client"
PHONE_CONVERT = f"/api/v1/inquiries/{INQUIRY_ID}/conversion"


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

        origin = f"http://127.0.0.1:{port}"
        browser = build_opener(ProxyHandler({}), HTTPCookieProcessor())

        def post_form(path, fields):
            with browser.open(origin + path, urlencode(fields).encode(), timeout=15) as page:
                return urlsplit(page.url).path

        phone_ids: list[tuple[int, int]] = []
        arrivals = asyncio.Barrier(2)
        web_entered = False

        @app.middleware("http")
        async def race_conversion(request, call_next):
            nonlocal web_entered
            path = request.url.path
            if request.method == "POST" and path == WEB_CONVERT and not web_entered:
                web_entered = True
                await asyncio.wait_for(arrivals.wait(), timeout=10)
            if request.method != "PUT" or path != PHONE_CONVERT:
                return await call_next(request)

            async def phone_conversion():
                # Release both real HTTP requests only when both have reached this studio.
                await asyncio.wait_for(arrivals.wait(), timeout=10)
                return await call_next(request)

            first_request = not web_entered
            if first_request:
                response, destination = await asyncio.gather(
                    phone_conversion(), asyncio.to_thread(post_form, WEB_CONVERT, {})
                )
            else:
                response = await call_next(request)
            if response.status_code != 200:
                return response
            body = b"".join([chunk async for chunk in response.body_iterator])
            summary = json.loads(body)["summary"]
            client_id, project_id = summary["converted_client_id"], summary["converted_project_id"]
            if not first_request:
                phone_ids.append((client_id, project_id))
                print(
                    f"iPhone conversion retry returned client {client_id}, project {project_id}.",
                    flush=True,
                )
                return Response(body, status_code=response.status_code, headers=response.headers)

            expected = f"/admin/studio/projects/{project_id}"
            retried = await asyncio.to_thread(post_form, WEB_CONVERT, {})
            if client_id != CLIENT_ID or destination != expected or retried != expected:
                raise RuntimeError(
                    f"Conversions disagreed: iPhone {(client_id, project_id)}, web {destination}, {retried}"
                )
            phone_ids.append((client_id, project_id))
            print(
                f"Concurrent iPhone and browser conversion: client {client_id}, project {project_id}; browser retry kept {retried}.",
                flush=True,
            )
            # The write committed. Keep the iPhone on its original inquiry so it retries the command.
            return Response(status_code=503, headers={"Cache-Control": "no-store"})

        project_reads: list[tuple[str, int]] = []

        @app.middleware("http")
        async def record_project_read(request, call_next):
            response = await call_next(request)
            path = request.url.path
            if request.method == "GET" and path.startswith("/api/v1/projects/"):
                # URLSession may revalidate the first response with If-None-Match.
                origin_status = response.status_code
                if origin_status in (200, 304) and project_reads == [(path, 200)]:
                    linked = db.one(
                        "SELECT converted_project_id FROM inquiries WHERE id=?", (INQUIRY_ID,)
                    )
                    if path == f"/api/v1/projects/{linked['converted_project_id']}":
                        response = Response(status_code=503, headers={"Cache-Control": "no-store"})
                project_reads.append((path, response.status_code))
                print(
                    f"Simulator GET {path}: studio {origin_status}, sent {response.status_code}",
                    flush=True,
                )
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
            if post_form("/admin/login", {"password": password}) == "/admin/login":
                raise RuntimeError("Browser login to the synthetic studio failed.")
            yield port, password, project_reads, phone_ids
        finally:
            server.should_exit = True
            thread.join()


def verify_journey(project_reads, phone_ids):
    from app import db

    row = db.one(
        """SELECT i.converted_client_id, p.id AS project_id, p.title
             FROM inquiries i JOIN projects p
               ON p.id=i.converted_project_id AND p.client_id=i.converted_client_id
            WHERE i.id=?""",
        (INQUIRY_ID,),
    )
    if row is None or row["converted_client_id"] != CLIENT_ID or row["title"] != PROJECT_TITLE:
        raise RuntimeError("The simulator did not convert the booking into the expected records.")
    expected_ids = (CLIENT_ID, row["project_id"])
    if phone_ids != [expected_ids, expected_ids]:
        raise RuntimeError(f"The retry changed the original conversion: {phone_ids!r}")
    clients = db.all_(
        "SELECT id FROM clients WHERE email=(SELECT email FROM inquiries WHERE id=?)", (INQUIRY_ID,)
    )
    if [client["id"] for client in clients] != [CLIENT_ID]:
        raise RuntimeError("Expected conversion to keep the original, matching client.")
    projects = db.all_("SELECT id FROM projects WHERE client_id=? ORDER BY id", (CLIENT_ID,))
    audits = db.one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='inquiry' AND entity_id=? AND action='convert'",
        (INQUIRY_ID,),
    )
    if [p["id"] for p in projects] != [INQUIRY_ID, row["project_id"]] or audits["n"] != 1:
        raise RuntimeError(
            "Expected just the original decoy, one converted project, and one conversion audit."
        )
    path = f"/api/v1/projects/{row['project_id']}"
    if project_reads not in (
        [(path, 200), (path, 503), (path, 200)],
        [(path, 200), (path, 503), (path, 304)],
    ):
        raise RuntimeError(f"Unexpected simulator project reads: {project_reads!r}")
    print(
        f"Verified inquiry {INQUIRY_ID} opened its stored project {row['project_id']}, "
        f"retained client {CLIENT_ID} after both conversion retries, and recovered "
        "from the synthetic project-read 503 on the second visit."
    )


def main(reference_udid: str) -> int:
    with (
        empty_iphone(reference_udid) as udid,
        synthetic_studio() as (port, password, project_reads, phone_ids),
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
        verify_journey(project_reads, phone_ids)
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: run_journey.py <iPhone simulator UDID>")
    # Unwind the fixture and simulator contexts when a CI job is cancelled.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    sys.exit(main(sys.argv[1]))
