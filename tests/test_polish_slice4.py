"""Polish Slice 4: hosted studios wear their own name.

Found by the release dry-run's screenshots: templates resolved `site_name`
from the process-global config.SITE_NAME, so every hosted studio's admin
topbar, PIN-page <title>, branded error pages, and receipts showed the
OPERATOR's studio name to another studio's clients. render._site_name() is
the template twin of mailer.sender_name() (ADR 0055): tenant's studio_name
in a hosted tenant context, operator's SITE_NAME everywhere else.
"""

import pytest

from app import config, render, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "slice4p-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def test_hosted_tenant_pages_carry_the_studios_own_name(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SITE_NAME", "Operator Studio")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime("alpha"):
        assert render._site_name() == "Alpha Studio"


def test_platform_and_single_tenant_keep_operator_site_name(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_NAME", "Operator Studio")
    # single-tenant: unchanged behavior
    monkeypatch.setattr(config, "SAAS_MODE", False)
    assert render._site_name() == "Operator Studio"
    # hosted platform context (marketing/root host, no tenant): operator's name
    _configure_saas(tmp_path, monkeypatch)
    assert render._site_name() == "Operator Studio"
