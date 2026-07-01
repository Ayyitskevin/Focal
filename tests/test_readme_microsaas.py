from pathlib import Path


def test_readme_keeps_flat_hosted_positioning():
    text = Path("README.md").read_text()

    required = [
        "Exactly **$20/month**",
        "No paid tiers",
        "14-day trial",
        "python scripts/hosted-preflight.py",
        "/admin/saas",
        "/demo",
        "docs/LAUNCH-KIT.md",
        "docs/BETA-LAUNCH.md",
        "scripts/launch-hosted-production.sh",
        "utm_source",
        "operator growth analytics",
        "trial nudge",
        "/admin/saas/export.csv",
        "MISE_SAAS_ANNOUNCEMENT",
        "MISE_SAAS_MODE=true",
        "2000",
    ]
    for phrase in required:
        assert phrase in text


def test_launch_kit_keeps_public_launch_assets():
    text = Path("docs/LAUNCH-KIT.md").read_text()

    required = [
        "Exactly `$20/month`",
        "14-day free trial",
        "No paid tiers",
        "MISE_CADDY_SITE_ADDRESS",
        "5-Post X Launch Thread",
        "Prioritized 7-Day Launch Checklist",
        "python scripts/hosted-preflight.py",
        "scripts/launch-hosted-production.sh",
    ]
    for phrase in required:
        assert phrase in text


def test_beta_launch_docs_keep_invite_and_security_checklist():
    text = Path("docs/BETA-LAUNCH.md").read_text()
    script = Path("scripts/launch-hosted-production.sh").read_text()

    required = [
        "Security & Pre-Launch Checklist",
        "Beta Invitation Email",
        "5-10 trusted photographers",
        "MISE_COOKIE_SECURE=true",
        "exactly `$20/month`",
        "Beta Acquisition Links",
        "at-risk trials",
        "python scripts/hosted-preflight.py",
    ]
    for phrase in required:
        assert phrase in text
    assert "docker compose" in script
    assert "python scripts/hosted-preflight.py" in script
