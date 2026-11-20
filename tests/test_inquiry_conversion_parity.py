"""The browser and iPhone must agree on the records created by one conversion."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app import audit, config, db, ratelimit
from app.main import app


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "studio.db")
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    db.migrate()
    ratelimit._hits.clear()
    browser = TestClient(
        app, base_url=config.BASE_URL, follow_redirects=False, raise_server_exceptions=False
    )
    iphone = TestClient(app, base_url=config.BASE_URL, raise_server_exceptions=False)
    try:
        assert (
            browser.post("/admin/login", data={"password": config.ADMIN_PASSWORD}).status_code
            == 303
        )
        login = iphone.post(
            "/api/v1/auth/studio/login",
            json={
                "email": None,
                "password": config.ADMIN_PASSWORD,
                "device": {
                    "installation_id": "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98",
                    "name": "Owner iPhone",
                    "platform": "ios",
                    "app_version": "1.0",
                },
            },
        )
        assert login.status_code == 200
        iphone.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        yield browser, iphone
    finally:
        browser.close()
        iphone.close()
        ratelimit._hits.clear()


def booking():
    return db.run(
        """INSERT INTO inquiries(name,email,message,kind,service,shoot_date)
           VALUES ('Studio','studio@example.test','Portrait session','booking','Portraits','2030-10-13')"""
    )


def web_convert(browser, iid, **form):
    result = browser.post(f"/admin/studio/inquiries/{iid}/client", data=form)
    assert result.status_code == 303
    return result.headers["location"]


def phone_convert(iphone, iid):
    result = iphone.put(f"/api/v1/inquiries/{iid}/conversion")
    assert result.status_code == 200
    summary = result.json()["summary"]
    assert summary["status"] == "converted"
    return summary["converted_client_id"], summary["converted_project_id"]


def stored_ids(iid):
    return tuple(
        db.one("SELECT converted_client_id, converted_project_id FROM inquiries WHERE id=?", (iid,))
    )


def assert_booking_records(iid, client_id, project_id):
    assert stored_ids(iid) == (client_id, project_id)
    assert [row["id"] for row in db.all_("SELECT id FROM clients")] == [client_id]
    assert [tuple(row) for row in db.all_("SELECT id,client_id FROM projects")] == [
        (project_id, client_id)
    ]
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='inquiry' AND entity_id=? AND action='convert'",
            (iid,),
        )["n"]
        == 1
    )


@pytest.mark.parametrize("first", ["browser", "iphone"])
def test_each_surface_retries_the_original_booking(owners, first):
    browser, iphone = owners
    cid = db.run(
        "INSERT INTO clients(id,name,email) VALUES (47,'Existing Studio','studio@example.test')"
    )
    iid = booking()
    if first == "iphone":
        original = phone_convert(iphone, iid)
    else:
        redirect = web_convert(browser, iid)
        original = stored_ids(iid)
        assert redirect == f"/admin/studio/projects/{original[1]}"
    assert original[0] == cid and original[1] is not None
    # A retry within the same clock second could otherwise overwrite the stamp unnoticed.
    original_stamp = "2001-02-03 04:05:06"
    db.run("UPDATE inquiries SET converted_at=? WHERE id=?", (original_stamp, iid))

    assert phone_convert(iphone, iid) == original
    assert web_convert(browser, iid) == f"/admin/studio/projects/{original[1]}"
    assert web_convert(browser, iid, return_to="/admin/inbox") == "/admin/inbox"
    assert_booking_records(iid, *original)
    assert (
        db.one("SELECT converted_at FROM inquiries WHERE id=?", (iid,))["converted_at"]
        == original_stamp
    )


@pytest.mark.parametrize(("first", "second"), [("iphone", "browser"), ("browser", "iphone")])
def test_browser_and_iphone_contend_for_the_same_booking(owners, monkeypatch, first, second):
    browser, iphone = owners
    iid = booking()
    first_holds_write = Event()
    second_attempted_write = Event()
    release_first = Event()
    real_audit = audit.log
    real_connect = db.connect

    def pause_first_conversion(con, entity_type, entity_id, action, **kwargs):
        real_audit(con, entity_type, entity_id, action, **kwargs)
        if action == "convert" and not first_holds_write.is_set():
            first_holds_write.set()
            assert release_first.wait(timeout=10)

    def observe_write(statement):
        # SQLite calls its trace hook before executing the contender's write.
        if first_holds_write.is_set() and statement.lstrip().upper().startswith(
            ("BEGIN IMMEDIATE", "INSERT INTO CLIENTS", "INSERT INTO PROJECTS")
        ):
            second_attempted_write.set()

    def traced_connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.set_trace_callback(observe_write)
        return con

    monkeypatch.setattr(audit, "log", pause_first_conversion)
    monkeypatch.setattr(db, "connect", traced_connect)
    calls = {"browser": (web_convert, browser, iid), "iphone": (phone_convert, iphone, iid)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {first: pool.submit(*calls[first])}
        try:
            assert first_holds_write.wait(timeout=10)
            pending[second] = pool.submit(*calls[second])
            assert second_attempted_write.wait(timeout=10)
        finally:
            release_first.set()
        original = pending["iphone"].result(timeout=10)
        assert pending["browser"].result(timeout=10) == f"/admin/studio/projects/{original[1]}"
    assert phone_convert(iphone, iid) == original
    assert web_convert(browser, iid) == f"/admin/studio/projects/{original[1]}"
    assert_booking_records(iid, *original)


def test_blank_email_contacts_never_share_clients_or_create_projects(owners):
    browser, iphone = owners
    unrelated = db.run("INSERT INTO clients(name,email) VALUES ('Other client','')")
    first, second = [
        db.run(
            "INSERT INTO inquiries(name,email,message,kind) VALUES (?,?, 'Contact','contact')", pair
        )
        for pair in [("First contact", ""), ("Second contact", "   ")]
    ]
    first_redirect = web_convert(browser, first)
    first_client, project = stored_ids(first)
    assert first_client != unrelated and project is None
    assert first_redirect == f"/admin/studio/clients/{first_client}"
    second_client, project = phone_convert(iphone, second)
    assert second_client not in (unrelated, first_client) and project is None

    assert phone_convert(iphone, first) == (first_client, None)
    assert web_convert(browser, second) == f"/admin/studio/clients/{second_client}"
    assert stored_ids(second) == (second_client, None)
    assert [
        (row["id"], row["name"]) for row in db.all_("SELECT id,name FROM clients ORDER BY id")
    ] == [
        (unrelated, "Other client"),
        (first_client, "First contact"),
        (second_client, "Second contact"),
    ]
    assert db.all_("SELECT id FROM projects") == []


def test_deleted_conversion_links_are_not_recreated(owners):
    browser, iphone = owners
    iid = booking()
    client_id, project_id = phone_convert(iphone, iid)
    stamp = "2001-02-03 04:05:06"
    db.run("UPDATE inquiries SET converted_at=? WHERE id=?", (stamp, iid))
    db.run("DELETE FROM projects WHERE id=?", (project_id,))
    assert web_convert(browser, iid) == f"/admin/studio/clients/{client_id}"
    assert phone_convert(iphone, iid) == (client_id, None)
    db.run("DELETE FROM clients WHERE id=?", (client_id,))
    for form in ({}, {"return_to": f"/admin/inbox?tab=all&sel={iid}"}):
        replay = browser.post(f"/admin/studio/inquiries/{iid}/client", data=form)
        assert replay.status_code == 410
        assert "linked records are no longer available" in replay.text
        assert "location" not in replay.headers
    assert phone_convert(iphone, iid) == (None, None)
    assert db.one("SELECT converted_at FROM inquiries WHERE id=?", (iid,))["converted_at"] == stamp
    assert db.all_("SELECT id FROM clients") == []
    assert db.all_("SELECT id FROM projects") == []
    assert len(db.all_("SELECT id FROM audit_log WHERE action='convert'")) == 1


@pytest.mark.parametrize("surface", ["browser", "iphone"])
def test_audit_failure_rolls_back_all_conversion_writes(owners, surface):
    browser, iphone = owners
    iid = booking()
    db.run("""CREATE TRIGGER reject_conversion_audit BEFORE INSERT ON audit_log
              WHEN NEW.action='convert' BEGIN SELECT RAISE(ABORT, 'test audit failure'); END""")
    if surface == "browser":
        response = browser.post(f"/admin/studio/inquiries/{iid}/client")
    else:
        response = iphone.put(f"/api/v1/inquiries/{iid}/conversion")
    assert response.status_code == 500
    assert stored_ids(iid) == (None, None)
    assert db.one("SELECT converted_at FROM inquiries WHERE id=?", (iid,))["converted_at"] is None
    assert db.all_("SELECT id FROM clients") == []
    assert db.all_("SELECT id FROM projects") == []
    assert db.all_("SELECT id FROM audit_log WHERE action='convert'") == []
