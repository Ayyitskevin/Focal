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
    ]
    for phrase in required:
        assert phrase in text
