"""A scheduler confirmation and an owner's conversion publish a single Studio identity."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app import booking_notify, config, db, ratelimit
from app.main import app


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "studio.db")
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(booking_notify.mailer, "configured", lambda: False)
    monkeypatch.setattr(booking_notify.notion_sync, "sync_booking", lambda _: None)
    monkeypatch.setattr(booking_notify.notion_sync, "sync_session_for_booking", lambda _: None)
    monkeypatch.setattr(booking_notify.gcal, "on_booking_confirmed", lambda _: None)
    db.migrate()
    ratelimit._hits.clear()
    client = TestClient(app, base_url=config.BASE_URL, follow_redirects=False)
    try:
        assert (
            client.post("/admin/login", data={"password": config.ADMIN_PASSWORD}).status_code == 303
        )
        yield client
    finally:
        client.close()
        ratelimit._hits.clear()


def booking(*, shoot=True, mirror=False, source=None):
    slug = "replacement" if source else "original"
    event_id = db.run(
        "INSERT INTO event_types (slug,name,creates_notion_session) VALUES (?,?,?)",
        (slug, "Portraits", int(shoot)),
    )
    iid = None
    if mirror:
        iid = db.run(
            """INSERT INTO inquiries (name,email,message,kind,shoot_date,service)
               VALUES ('Studio','studio@example.test','Intake','booking','2030-10-13','Portraits')"""
        )
    bid = db.run(
        """INSERT INTO bookings (token,event_type_id,name,email,start_utc,end_utc,tz,
                                 inquiry_id,reschedule_of,phone,venue_address)
           VALUES (?,?,'Studio','studio@example.test','2030-10-13 12:00:00',
                   '2030-10-13 13:00:00','UTC',?,?,'555-0102','Studio B')""",
        (slug, event_id, iid, source),
    )
    return bid, iid


def owner_convert(browser, inquiry_id):
    response = browser.post(f"/admin/studio/inquiries/{inquiry_id}/client")
    assert response.status_code == 303
    return response.headers["location"]


def booking_links(bid):
    return tuple(
        db.one("SELECT inquiry_id, client_id, project_id FROM bookings WHERE id=?", (bid,))
    )


def inquiry_links(iid):
    return tuple(
        db.one("SELECT converted_client_id, converted_project_id FROM inquiries WHERE id=?", (iid,))
    )


@pytest.mark.parametrize("first", ["scheduler", "owner"])
def test_scheduler_and_owner_contend_for_one_identity(browser, monkeypatch, first):
    # An unrelated project on the same client must never be reused for this shoot.
    cid = db.run("INSERT INTO clients(id,name,email) VALUES (47,'Studio','studio@example.test')")
    decoy = db.run("INSERT INTO projects(id,client_id,title) VALUES (74,?,'Another shoot')", (cid,))
    bid, iid = booking(mirror=True)
    holding_lock = Event()
    contender_writing = Event()
    release = Event()
    first_project = []

    def hold_first_project(pid):
        if not first_project:
            first_project.append(pid)
            holding_lock.set()
            if not release.wait(10):
                raise RuntimeError("competing writer did not arrive")
        return 0

    def trace(statement):
        if holding_lock.is_set() and statement.strip().upper().startswith(
            ("BEGIN IMMEDIATE", "INSERT INTO PROJECTS")
        ):
            contender_writing.set()

    # The trigger has the actual new project ID and keeps its transaction open.
    # SQLite traces the second writer's statement before waiting for the write lock.
    db.run("""CREATE TRIGGER pause_project AFTER INSERT ON projects
              BEGIN SELECT hold_first_project(NEW.id); END""")
    real_connect = db.connect

    def connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.create_function("hold_first_project", 1, hold_first_project)
        con.set_trace_callback(trace)
        return con

    monkeypatch.setattr(db, "connect", connect)
    actions = {
        "scheduler": (booking_notify._link_studio, bid, iid),
        "owner": (owner_convert, browser, iid),
    }
    second = "owner" if first == "scheduler" else "scheduler"
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {first: pool.submit(*actions[first])}
        try:
            assert holding_lock.wait(10), f"{first} never wrote a project"
            pending[second] = pool.submit(*actions[second])
            assert contender_writing.wait(10), f"{second} never attempted to write"
        finally:
            release.set()
        pending["scheduler"].result(timeout=10)
        destination = pending["owner"].result(timeout=10)

    pid = first_project[0]
    assert destination == f"/admin/studio/projects/{pid}"
    assert booking_links(bid) == (iid, cid, pid)
    assert inquiry_links(iid) == (cid, pid)
    shoot = db.one("SELECT shoot_date, notes FROM projects WHERE id=?", (pid,))
    assert shoot["shoot_date"] == "2030-10-13"
    assert "Studio B" in shoot["notes"]
    original_stamp = "2025-02-03 04:05:06"
    db.run("UPDATE inquiries SET converted_at=? WHERE id=?", (original_stamp, iid))
    db.run("UPDATE projects SET title='Edited title' WHERE id=?", (pid,))

    booking_notify.confirm(bid)
    assert owner_convert(browser, iid) == destination
    assert booking_links(bid) == (iid, cid, pid)
    assert inquiry_links(iid) == (cid, pid)
    assert [tuple(row) for row in db.all_("SELECT id,converted_at FROM inquiries")] == [
        (iid, original_stamp)
    ]
    assert [row["id"] for row in db.all_("SELECT id FROM clients")] == [cid]
    assert [
        tuple(row) for row in db.all_("SELECT id,client_id,title FROM projects ORDER BY id")
    ] == [
        (decoy, cid, "Another shoot"),
        (pid, cid, "Edited title"),
    ]


def test_confirm_replay_reuses_generated_mirror_and_project(browser):
    bid, _ = booking()
    booking_notify.confirm(bid)
    iid, cid, pid = booking_links(bid)
    assert all((iid, cid, pid))
    assert inquiry_links(iid) == (cid, pid)
    assert db.one("SELECT phone FROM clients WHERE id=?", (cid,))["phone"] == "555-0102"
    booking_notify.confirm(bid)
    assert booking_links(bid) == (iid, cid, pid)
    assert owner_convert(browser, iid) == f"/admin/studio/projects/{pid}"
    assert [row["id"] for row in db.all_("SELECT id FROM inquiries")] == [iid]
    assert [row["id"] for row in db.all_("SELECT id FROM clients")] == [cid]
    assert [row["id"] for row in db.all_("SELECT id FROM projects")] == [pid]


def test_failed_mirror_can_be_retried(browser, caplog):
    bid, _ = booking()
    db.run("""CREATE TRIGGER fail_mirror BEFORE INSERT ON inquiries
              BEGIN SELECT RAISE(ABORT, 'injected mirror failure'); END""")
    booking_notify.confirm(bid)
    assert "injected mirror failure" in caplog.text
    assert booking_links(bid) == (None, None, None)
    assert db.all_("SELECT id FROM inquiries") == []
    assert db.all_("SELECT id FROM clients") == []
    assert db.all_("SELECT id FROM projects") == []
    db.run("DROP TRIGGER fail_mirror")

    booking_notify.confirm(bid)
    iid, cid, pid = booking_links(bid)
    assert all((iid, cid, pid))
    assert inquiry_links(iid) == (cid, pid)
    assert owner_convert(browser, iid) == f"/admin/studio/projects/{pid}"
    assert [row["id"] for row in db.all_("SELECT id FROM projects")] == [pid]


@pytest.mark.parametrize("old_inquiry", [None, 912])
def test_new_mirror_adopts_existing_booking_records(browser, old_inquiry):
    # Older confirmations could finish linking a project after their mirror failed.
    bid, _ = booking()
    cid = db.run("INSERT INTO clients(name) VALUES ('Existing client')")
    pid = db.run("INSERT INTO projects(client_id,title) VALUES (?,'Edited shoot')", (cid,))
    db.run(
        "UPDATE bookings SET inquiry_id=?,client_id=?,project_id=? WHERE id=?",
        (old_inquiry, cid, pid, bid),
    )
    booking_notify.confirm(bid)
    iid, linked_client, linked_project = booking_links(bid)
    assert iid and iid != old_inquiry
    assert (linked_client, linked_project) == inquiry_links(iid) == (cid, pid)
    assert owner_convert(browser, iid) == f"/admin/studio/projects/{pid}"
    assert [tuple(r) for r in db.all_("SELECT id,title FROM projects")] == [(pid, "Edited shoot")]


def test_scheduler_replay_respects_an_explicit_owner_undo(browser):
    bid, _ = booking()
    booking_notify.confirm(bid)
    iid, cid, pid = booking_links(bid)
    assert all((iid, cid, pid))
    assert browser.post(f"/admin/studio/inquiries/{iid}/unconvert").status_code == 303
    booking_notify.confirm(bid)
    assert booking_links(bid) == (iid, cid, pid)
    assert inquiry_links(iid) == (None, None)
    assert db.one("SELECT converted_at FROM inquiries WHERE id=?", (iid,))[0] is None
    assert owner_convert(browser, iid) == f"/admin/studio/projects/{pid}"
    assert inquiry_links(iid) == (cid, pid)
    assert [row["id"] for row in db.all_("SELECT id FROM projects")] == [pid]


@pytest.mark.parametrize("first", ["scheduler", "owner"])
def test_consultation_waits_for_explicit_promotion(browser, first):
    bid, iid = booking(shoot=False, mirror=True)
    scheduler_cid = None
    if first == "scheduler":
        booking_notify._link_studio(bid, iid)
        _, scheduler_cid, pid = booking_links(bid)
        assert scheduler_cid and pid is None
        assert (
            db.one("SELECT converted_at FROM inquiries WHERE id=?", (iid,))["converted_at"] is None
        )
        assert db.all_("SELECT id FROM projects") == []

    destination = owner_convert(browser, iid)
    cid, pid = inquiry_links(iid)
    assert cid and pid
    if scheduler_cid is not None:
        assert cid == scheduler_cid
    assert db.one("SELECT phone FROM clients WHERE id=?", (cid,))["phone"] == "555-0102"
    assert destination == f"/admin/studio/projects/{pid}"
    assert booking_links(bid) == (iid, cid, pid)
    booking_notify.confirm(bid)
    assert booking_links(bid) == (iid, cid, pid)
    assert [row["id"] for row in db.all_("SELECT id FROM projects")] == [pid]


@pytest.mark.parametrize("first", ["scheduler", "owner"])
def test_rescheduled_shoot_uses_the_source_project(browser, first):
    source, _ = booking()
    booking_notify.confirm(source)
    original_inquiry, cid, pid = booking_links(source)
    assert original_inquiry and cid and pid
    replacement, iid = booking(source=source, mirror=True)
    if first == "owner":
        assert owner_convert(browser, iid) == f"/admin/studio/projects/{pid}"
    booking_notify.confirm(replacement)
    assert booking_links(replacement) == (iid, cid, pid)
    assert inquiry_links(iid) == (cid, pid)
    assert owner_convert(browser, iid) == f"/admin/studio/projects/{pid}"
    assert [row["id"] for row in db.all_("SELECT id FROM projects")] == [pid]


def test_failed_confirmation_write_rolls_back_and_can_retry(browser, caplog):
    bid, iid = booking(mirror=True)
    db.run("""CREATE TRIGGER abort_booking_link BEFORE UPDATE OF client_id,project_id ON bookings
              WHEN NEW.project_id IS NOT NULL
              BEGIN SELECT RAISE(ABORT, 'injected booking link failure'); END""")
    booking_notify.confirm(bid)
    assert "injected booking link failure" in caplog.text
    assert db.all_("SELECT id FROM projects") == []
    assert db.all_("SELECT id FROM clients") == []
    assert booking_links(bid) == (iid, None, None)
    assert inquiry_links(iid) == (None, None)
    assert db.one("SELECT converted_at FROM inquiries WHERE id=?", (iid,))["converted_at"] is None
    db.run("DROP TRIGGER abort_booking_link")

    booking_notify.confirm(bid)
    mirror, cid, pid = booking_links(bid)
    assert mirror == iid and cid and pid
    assert inquiry_links(iid) == (cid, pid)
    assert owner_convert(browser, iid) == f"/admin/studio/projects/{pid}"
    assert [row["id"] for row in db.all_("SELECT id FROM inquiries")] == [iid]
    assert [row["id"] for row in db.all_("SELECT id FROM clients")] == [cid]
    assert [row["id"] for row in db.all_("SELECT id FROM projects")] == [pid]
