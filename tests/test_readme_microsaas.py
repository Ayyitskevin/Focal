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
        "MISE_SAAS_MODE=true",
        "2000",
    ]
    for phrase in required:
        assert phrase in text
