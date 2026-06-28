"""Retainer deepening — DB-backed routes + lifecycle (admin), same pattern as
test_smoke_money_ops.py. Proves: term/renewal fields persist with a one-shot nudge that re-arms
only when the date changes; the per-period quota snapshot is written once and overage is measured
against it; the Renew action rolls the term; the pause-at-term guard skips the unattended sweep
but not a deliberate manual generate; and the renewal nudge fires once. Nothing here sends or
charges — generation stays draft-only (§11.4)."""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.admin import recurring
from app.main import app


def _configure_tmp_db(tmp_path, monkeypatch):
    for attr, val in {
        "DATA_DIR": tmp_path,
        "DB_PATH": tmp_path / "mise.db",
        "MEDIA_DIR": tmp_path / "media",
        "ZIP_DIR": tmp_path / "zips",
        "TMP_DIR": tmp_path / "tmp",
        "BRAND_DIR": tmp_path / "brand",
        "RECEIPTS_DIR": tmp_path / "receipts",
        "SECRET_KEY": "test-secret",
        "ADMIN_PASSWORD": "test-pw",
    }.items():
        monkeypatch.setattr(config, attr, val)
    db.migrate()


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
        assert r.status_code == 303
        yield client
    jobs.stop()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit

    ratelimit._hits.clear()
    yield


def _plan(*, quota=None, total_cents=200000, **cols):
    cid = db.run("INSERT INTO clients (name) VALUES (?)", ("Blue Plate Co",))
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, "Retainer"))
    keys = ["project_id", "title", "total_cents", "line_items", "quota", *cols.keys()]
    vals = [
        pid,
        "Blue Plate Monthly",
        total_cents,
        json.dumps([{"label": "Retainer", "qty": 1, "unit_cents": total_cents}]),
        json.dumps(quota or []),
        *cols.values(),
    ]
    ph = ",".join("?" * len(keys))
    plan_id = db.run(f"INSERT INTO recurring_plans ({','.join(keys)}) VALUES ({ph})", tuple(vals))
    return plan_id


def _update_form(**over) -> dict:
    form = {"title": "Blue Plate Monthly", "anchor_day": "1", "active": "1"}
    form.update(over)
    return form


# --- term fields + one-shot nudge reset -------------------------------------


def test_update_plan_persists_term_and_pause(admin_client):
    pid = _plan()
    r = admin_client.post(
        f"/admin/studio/recurring/{pid}",
        data=_update_form(term_start="2026-01-01", renews_on="2027-01-01", pause_at_term="1"),
    )
    assert r.status_code in (200, 303)
    row = db.one(
        "SELECT term_start, renews_on, pause_at_term FROM recurring_plans WHERE id=?", (pid,)
    )
    assert row["term_start"] == "2026-01-01" and row["renews_on"] == "2027-01-01"
    assert row["pause_at_term"] == 1


def test_nudge_rearms_only_when_renewal_date_changes(admin_client):
    pid = _plan(renews_on="2027-01-01", nudged_renewal=1)
    # editing the title (renews_on unchanged) must NOT re-arm the one-shot nudge
    admin_client.post(
        f"/admin/studio/recurring/{pid}", data=_update_form(title="Renamed", renews_on="2027-01-01")
    )
    assert (
        db.one("SELECT nudged_renewal FROM recurring_plans WHERE id=?", (pid,))["nudged_renewal"]
        == 1
    )
    # moving the renewal date DOES re-arm it
    admin_client.post(f"/admin/studio/recurring/{pid}", data=_update_form(renews_on="2027-06-01"))
    assert (
        db.one("SELECT nudged_renewal FROM recurring_plans WHERE id=?", (pid,))["nudged_renewal"]
        == 0
    )


# --- per-period quota snapshot + overage ------------------------------------


def test_generate_snapshots_quota_once_and_overage_uses_it(admin_client):
    pid = _plan(
        quota=[{"label": "Hero", "target": 20, "unit": "images", "overage_rate_cents": 1500}]
    )
    plan = recurring.get_plan(pid)
    period = recurring._period()
    iid = recurring.generate_for_plan(plan, period)
    assert iid is not None
    snaps = db.all_(
        "SELECT quota_json FROM retainer_period_quota WHERE plan_id=? AND period=?", (pid, period)
    )
    assert len(snaps) == 1  # written once
    # now EDIT the live quota (raise the target) — the snapshot must not move
    db.run(
        "UPDATE recurring_plans SET quota=? WHERE id=?",
        (
            json.dumps(
                [{"label": "Hero", "target": 99, "unit": "images", "overage_rate_cents": 1500}]
            ),
            pid,
        ),
    )
    # log 22 delivered: against the SNAPSHOT target (20) that's 2 over = $30, not against live (99)
    db.run(
        "INSERT INTO retainer_deliveries (plan_id, period, label, qty) VALUES (?,?,?,?)",
        (pid, period, "Hero", 22),
    )
    out = recurring.compute_overage(recurring.get_plan(pid), period)
    assert out["snapshot"] is True
    assert out["total_cents"] == 3000


def test_generate_is_idempotent_on_snapshot(admin_client):
    pid = _plan(quota=[{"label": "Hero", "target": 5}])
    plan = recurring.get_plan(pid)
    period = recurring._period()
    recurring.generate_for_plan(plan, period)
    recurring.generate_for_plan(recurring.get_plan(pid), period)  # claim already taken -> no-op
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM retainer_period_quota WHERE plan_id=? AND period=?",
            (pid, period),
        )["n"]
        == 1
    )


def test_overage_panel_renders(admin_client):
    pid = _plan(
        quota=[{"label": "Hero", "target": 20, "unit": "images", "overage_rate_cents": 1500}]
    )
    period = recurring._period()
    db.run(
        "INSERT INTO retainer_deliveries (plan_id, period, label, qty) VALUES (?,?,?,?)",
        (pid, period, "Hero", 23),
    )
    body = admin_client.get(f"/admin/studio/recurring/{pid}").text
    assert "Overage this period" in body
    assert "$45.00" in body  # 3 over * $15


# --- renew action -----------------------------------------------------------


def test_renew_rolls_term_and_clears_nudge(admin_client):
    pid = _plan(term_start="2026-01-01", renews_on="2027-01-01", nudged_renewal=1)
    r = admin_client.post(f"/admin/studio/recurring/{pid}/renew", follow_redirects=False)
    assert r.status_code == 303
    row = db.one(
        "SELECT term_start, renews_on, nudged_renewal FROM recurring_plans WHERE id=?", (pid,)
    )
    assert row["term_start"] == "2027-01-01" and row["renews_on"] == "2028-01-01"
    assert row["nudged_renewal"] == 0


# --- pause-at-term guard ----------------------------------------------------


def test_pause_at_term_skips_sweep_after_renewal_month(admin_client):
    today = dt.date.today()
    period_month = today.strftime("%Y-%m")
    # a plan whose term ended LAST month, pause_at_term on -> the sweep must skip it
    past = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m-01")
    paused = _plan(anchor_day=1, renews_on=past, pause_at_term=1)
    # an evergreen plan (no renews_on) -> the sweep still generates
    evergreen = _plan(anchor_day=1)
    n = recurring.run_due_plans(today)
    assert n >= 1
    assert (
        db.one("SELECT last_run_period FROM recurring_plans WHERE id=?", (paused,))[
            "last_run_period"
        ]
        is None
    )
    assert (
        db.one("SELECT last_run_period FROM recurring_plans WHERE id=?", (evergreen,))[
            "last_run_period"
        ]
        == period_month
    )


def test_pause_at_term_still_bills_renewal_month(admin_client):
    today = dt.date.today()
    this_month = today.strftime("%Y-%m-01")
    # term ends THIS month -> the renewal month itself still bills
    plan = _plan(anchor_day=1, renews_on=this_month, pause_at_term=1)
    recurring.run_due_plans(today)
    assert db.one("SELECT last_run_period FROM recurring_plans WHERE id=?", (plan,))[
        "last_run_period"
    ] == today.strftime("%Y-%m")


def test_manual_generate_overrides_pause(admin_client):
    today = dt.date.today()
    past = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m-01")
    pid = _plan(anchor_day=1, renews_on=past, pause_at_term=1)
    # the deliberate manual button is the human override — it still generates past term
    r = admin_client.post(f"/admin/studio/recurring/{pid}/generate", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/studio/invoices/")


# --- renewal nudge sweep ----------------------------------------------------


def test_renewal_nudge_fires_once(admin_client, monkeypatch):
    from app import alerts, retainer_reminders

    soon = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    pid = _plan(renews_on=soon)
    sent = []
    monkeypatch.setattr(alerts, "is_enabled", lambda: True)
    monkeypatch.setattr(alerts, "notify", lambda msg: sent.append(msg))
    retainer_reminders.sweep()
    assert len(sent) == 1 and "renewal" in sent[0].lower()
    assert (
        db.one("SELECT nudged_renewal FROM recurring_plans WHERE id=?", (pid,))["nudged_renewal"]
        == 1
    )
    retainer_reminders.sweep()  # one-shot — no second nudge
    assert len(sent) == 1


def test_renewal_nudge_skips_evergreen(admin_client, monkeypatch):
    from app import alerts, retainer_reminders

    _plan()  # no renews_on
    sent = []
    monkeypatch.setattr(alerts, "is_enabled", lambda: True)
    monkeypatch.setattr(alerts, "notify", lambda msg: sent.append(msg))
    retainer_reminders.sweep()
    assert sent == []


# --- overage -> draft pre-fill (the money seam; nothing is written until the human saves) ----


def _draft_with_overage(rate_cents=1500):
    """A plan with a rated quota, a generated draft, and a 3-over delivery this period ($45)."""
    pid = _plan(
        quota=[{"label": "Hero", "target": 20, "unit": "images", "overage_rate_cents": rate_cents}]
    )
    iid = recurring.generate_for_plan(recurring.get_plan(pid), recurring._period())
    db.run(
        "INSERT INTO retainer_deliveries (plan_id, period, label, qty) VALUES (?,?,?,?)",
        (pid, recurring._period(), "Hero", 23),
    )
    return pid, iid


def test_overage_to_draft_proposes_and_audits_without_writing(admin_client):
    pid, iid = _draft_with_overage()
    before = db.one("SELECT line_items FROM invoices WHERE id=?", (iid,))["line_items"]
    r = admin_client.post(f"/admin/studio/recurring/{pid}/overage-to-draft", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert f"/admin/studio/invoices/{iid}" in loc and "overage_unit_cents=4500" in loc
    # the action itself writes NO invoice line — only proposes
    assert db.one("SELECT line_items FROM invoices WHERE id=?", (iid,))["line_items"] == before
    # ...but records the figure proposed (audit fidelity for the money path)
    a = db.one(
        "SELECT action FROM audit_log WHERE entity_type='invoice' AND entity_id=? "
        "AND action='overage_proposed'",
        (iid,),
    )
    assert a is not None


def test_invoice_get_prefills_overage_row_without_persisting(admin_client):
    pid, iid = _draft_with_overage()
    before = db.one("SELECT line_items FROM invoices WHERE id=?", (iid,))["line_items"]
    body = admin_client.get(
        f"/admin/studio/invoices/{iid}?overage_label=Overage&overage_qty=1&overage_unit_cents=4500"
    ).text
    assert "Suggested overage line" in body
    assert "45.00" in body  # the pre-filled unit price renders in the editable row
    # the GET persisted nothing — the line is real only once the operator saves
    assert db.one("SELECT line_items FROM invoices WHERE id=?", (iid,))["line_items"] == before


def test_overage_prefill_ignored_on_locked_invoice(admin_client):
    pid, iid = _draft_with_overage()
    db.run("UPDATE invoices SET status='sent' WHERE id=?", (iid,))
    body = admin_client.get(
        f"/admin/studio/invoices/{iid}?overage_label=Overage&overage_qty=1&overage_unit_cents=4500"
    ).text
    assert "Suggested overage line" not in body  # never pre-fill a locked invoice


def test_overage_to_draft_refused_without_open_draft(admin_client):
    pid = _plan(
        quota=[{"label": "Hero", "target": 20, "unit": "images", "overage_rate_cents": 1500}]
    )
    db.run(
        "INSERT INTO retainer_deliveries (plan_id, period, label, qty) VALUES (?,?,?,?)",
        (pid, recurring._period(), "Hero", 23),
    )  # over-delivered, but no draft generated
    r = admin_client.post(f"/admin/studio/recurring/{pid}/overage-to-draft", follow_redirects=False)
    assert r.status_code == 303 and "overage_error" in r.headers["location"]


def test_overage_to_draft_refused_when_no_billable_overage(admin_client):
    pid, iid = _draft_with_overage(rate_cents=0)  # over, but no rate -> nothing billable
    r = admin_client.post(f"/admin/studio/recurring/{pid}/overage-to-draft", follow_redirects=False)
    assert r.status_code == 303 and "overage_error" in r.headers["location"]


def test_saving_prefilled_overage_persists_the_line(admin_client):
    # the loop closes only when the human SAVES: update_invoice writes the line + recomputes total
    pid, iid = _draft_with_overage()
    orig = json.loads(db.one("SELECT line_items FROM invoices WHERE id=?", (iid,))["line_items"])
    form = {"title": "Blue Plate Monthly", "deposit": "0", "net_days": "0"}
    for idx, it in enumerate(orig):
        form[f"item_label_{idx}"] = it["label"]
        form[f"item_qty_{idx}"] = str(it["qty"])
        form[f"item_price_{idx}"] = f"{it['unit_cents'] / 100:.2f}"
    n = len(orig)
    form[f"item_label_{n}"] = "Overage — extra Hero images"
    form[f"item_qty_{n}"] = "1"
    form[f"item_price_{n}"] = "45.00"
    r = admin_client.post(f"/admin/studio/invoices/{iid}", data=form, follow_redirects=False)
    assert r.status_code == 303
    items = json.loads(db.one("SELECT line_items FROM invoices WHERE id=?", (iid,))["line_items"])
    assert any(it["label"].startswith("Overage") and it["unit_cents"] == 4500 for it in items)
