from __future__ import annotations

import pytest

from app import db, onboarding, preset_packs


@pytest.fixture()
def isolated_db(tmp_path):
    token = db.set_request_db_path(tmp_path / "mise.db")
    db.migrate()
    yield
    db.reset_request_db_path(token)


def test_onboarding_status_starts_empty(isolated_db):
    status = onboarding.setup_status()

    assert status["total"] == 4
    assert not status["complete"]
    assert status["done"] == sum(1 for step in status["steps"] if step["done"])
    assert status["steps"][0]["key"] == "niche"
    assert status["steps"][0]["done"] is False
    assert status["steps"][2]["key"] == "project"
    assert status["steps"][2]["done"] is False
    assert status["steps"][3]["key"] == "delivery"
    assert status["steps"][3]["done"] is False


def test_onboarding_status_tracks_pack_project_and_delivery(isolated_db):
    preset_packs.install_pack("wedding")
    status = onboarding.setup_status()
    assert status["counts"]["packages"] == 2
    assert status["counts"]["workflow_rules"] == 4
    assert status["steps"][0]["done"] is True
    assert status["steps"][1]["done"] is True

    client_id = db.run("INSERT INTO clients (name, email) VALUES (?,?)", ("Ari", "ari@test"))
    gallery_id = db.run(
        "INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
        ("ari-gallery", "Ari Gallery", "1234"),
    )
    db.run(
        "INSERT INTO projects (client_id, title, status, gallery_id) VALUES (?,?,?,?)",
        (client_id, "Ari Wedding", "contract_signed", gallery_id),
    )

    complete = onboarding.setup_status()
    assert complete["done"] == 4
    assert complete["complete"] is True
