from __future__ import annotations

import pytest

from app import crm_fields, db, preset_packs, workflows
from app.public.packages import record_package_lead


@pytest.fixture()
def isolated_db(tmp_path):
    token = db.set_request_db_path(tmp_path / "mise.db")
    db.migrate()
    yield
    db.reset_request_db_path(token)


def _project() -> int:
    client_id = db.run(
        "INSERT INTO clients (name, email) VALUES (?,?)",
        ("Test Client", "client@example.com"),
    )
    return db.run(
        "INSERT INTO projects (client_id, title, status) VALUES (?,?,?)",
        (client_id, "Test Project", "inquiry_received"),
    )


def test_workflow_rules_create_visible_tasks_idempotently(isolated_db):
    project_id = _project()
    db.run(
        """INSERT INTO workflow_rules
           (name, trigger_key, task_title, delay_days)
           VALUES (?,?,?,?)""",
        ("Follow up", "proposal_sent", "Check in on proposal", 2),
    )

    assert workflows.fire_workflow("proposal_sent", project_id, ref_kind="proposal", ref_id=7) == 1
    assert workflows.fire_workflow("proposal_sent", project_id, ref_kind="proposal", ref_id=7) == 0

    task = db.one("SELECT * FROM tasks WHERE project_id=?", (project_id,))
    event = db.one("SELECT * FROM project_events WHERE project_id=?", (project_id,))
    assert task["title"] == "Check in on proposal"
    assert event["kind"] == "task"
    assert event["ref_kind"] == "task"
    assert event["ref_id"] == task["id"]


def test_preset_pack_installs_packages_rules_tags_and_forms(isolated_db):
    counts = preset_packs.install_pack("fnb")
    assert counts["packages"] == 2
    assert counts["workflow_rules"] == 3
    assert counts["tags"] == 2
    assert counts["forms"] == 1

    assert db.one("SELECT id FROM packages WHERE slug='menu-refresh'")
    assert db.one("SELECT id FROM workflow_rules WHERE trigger_key='gallery_published'")
    assert db.one("SELECT id FROM tags WHERE name='Food & Beverage'")
    form = db.one("SELECT id FROM forms WHERE slug='fnb-content-inquiry'")
    assert db.one("SELECT COUNT(*) AS n FROM form_fields WHERE form_id=?", (form["id"],))["n"] == 4

    second = preset_packs.install_pack("fnb")
    assert second["packages"] == 0
    assert second["workflow_rules"] == 0
    assert second["tags"] == 0
    assert second["forms"] == 0


def test_package_lead_lands_in_existing_inquiry_inbox(isolated_db):
    package_id = db.run(
        """INSERT INTO packages (slug, name, description, price_cents)
           VALUES (?,?,?,?)""",
        ("brand-portrait", "Brand Portrait", "A polished portrait session.", 85000),
    )

    lead_id = record_package_lead(
        package_id,
        name="Ari Lane",
        email="ari@example.com",
        event_date="2026-09-15",
        message="Need a homepage refresh.",
    )

    lead = db.one("SELECT * FROM package_leads WHERE id=?", (lead_id,))
    inquiry = db.one("SELECT * FROM inquiries WHERE id=?", (lead["inquiry_id"],))
    assert lead["name"] == "Ari Lane"
    assert inquiry["service"] == "Brand Portrait"
    assert "Need a homepage refresh." in inquiry["message"]


def test_project_custom_fields_and_client_tags(isolated_db):
    project_id = _project()
    project = db.one("SELECT * FROM projects WHERE id=?", (project_id,))

    crm_fields.upsert_project_field(project_id, "Venue", "The Orchard")
    crm_fields.upsert_project_field(project_id, "Venue", "The Foundry")
    tag_id = crm_fields.ensure_tag("Wedding", "#7C2F38")
    crm_fields.assign_client_tag(project["client_id"], tag_id)

    field = db.one("SELECT * FROM project_custom_fields WHERE project_id=?", (project_id,))
    tag = db.one(
        """SELECT t.name FROM tags t
           JOIN client_tags ct ON ct.tag_id=t.id
           WHERE ct.client_id=?""",
        (project["client_id"],),
    )
    assert field["field_value"] == "The Foundry"
    assert tag["name"] == "Wedding"
