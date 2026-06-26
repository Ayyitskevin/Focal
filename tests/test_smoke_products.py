"""Aphrodite products foundation — DB-backed: the deterministic guards, all dormant by default.

Proves the budget cap refuses over-spend (and refuses everything when disabled), the
review transitions record, and the export gate refuses until a render is BOTH approved and
consent-confirmed — so no image can be 'used' without an explicit human approval + consent,
and total spend can never exceed the cap. Nothing here is wired into the running app.
"""

import pytest

from app import config, db, products
from app.providers import Capability, registry
from app.providers.products_render import ProductsRenderAdapter


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


def _enable(monkeypatch, budget=10.0):
    monkeypatch.setattr(config, "PRODUCTS_RENDER_URL", "http://render.local")
    monkeypatch.setattr(config, "PRODUCTS_BUDGET_USD", budget)


def _gallery():
    return db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("ProdG", "P", "1"))


def test_disabled_by_default_refuses_everything(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    assert products.is_enabled() is False
    with pytest.raises(products.BudgetError):
        products.create_render(gid, cost_usd=1.0)
    assert db.one("SELECT COUNT(*) AS n FROM product_jobs")["n"] == 0


def test_budget_cap_is_enforced(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    _enable(monkeypatch, budget=10.0)
    gid = _gallery()
    products.create_render(gid, cost_usd=4.0)
    assert products.spend_to_date() == 4.0 and products.budget_remaining() == 6.0
    with pytest.raises(products.BudgetError):  # 7 > 6 remaining
        products.create_render(gid, cost_usd=7.0)
    products.create_render(gid, cost_usd=6.0)  # exactly to the cap
    assert products.budget_remaining() == 0.0
    with pytest.raises(products.BudgetError):  # nothing more fits
        products.create_render(gid, cost_usd=0.5)
    # only the two within-budget renders were written, and each recorded provenance
    assert db.one("SELECT COUNT(*) AS n FROM product_jobs")["n"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM ai_runs WHERE capability='products'")["n"] == 2


def test_export_gate_requires_approval_and_consent(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    _enable(monkeypatch)
    gid = _gallery()
    jid = products.create_render(gid, cost_usd=1.0)
    with pytest.raises(products.ExportError):  # draft -> refused
        products.export_job(jid)
    products.set_status(jid, "approved")
    with pytest.raises(products.ExportError):  # approved but consent not confirmed -> refused
        products.export_job(jid)
    products.confirm_consent(jid)
    out = products.export_job(jid)  # approved + consent -> exported
    assert out["exported_at"] is not None
    actions = {
        r["action"]
        for r in db.all_(
            "SELECT action FROM audit_log WHERE entity_type='product_job' AND entity_id=?", (jid,)
        )
    }
    assert {
        "product_render_created",
        "product_approved",
        "product_consent",
        "product_exported",
    } <= actions


def test_reject_blocks_export(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    _enable(monkeypatch)
    gid = _gallery()
    jid = products.create_render(gid, cost_usd=1.0)
    products.set_status(jid, "rejected")
    products.confirm_consent(jid)
    with pytest.raises(products.ExportError):  # rejected can never export
        products.export_job(jid)
    assert db.one("SELECT exported_at FROM product_jobs WHERE id=?", (jid,))["exported_at"] is None


def test_render_adapter_is_dormant():
    adapter = registry.resolve(Capability.PRODUCTS)
    assert isinstance(adapter, ProductsRenderAdapter)
    assert adapter.name == "aphrodite" and adapter.serves_production is False
    assert adapter.is_enabled() is False  # no PRODUCTS_RENDER_URL by default
    res = adapter.render(1, None, None)
    assert not res.ok and res.status.value == "disabled"
