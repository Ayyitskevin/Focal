import re
from pathlib import Path


def test_readme_keeps_truthful_focal_positioning():
    text = Path("README.md").read_text()

    required = [
        "active development",
        "private-beta preparation",
        "not a public",
        "self-hosted",
        "FOCAL-IDENTITY.md",
        "scripts/reviewer_demo.py",
        "/demo",
        "AGPL-3.0-only",
        "issues/182",
        "issues/185",
    ]
    for phrase in required:
        assert phrase in text
    assert "$20/month" not in text
    assert "iOS-first client & studio management" not in text


def test_reviewer_and_governance_docs_are_discoverable():
    readme = Path("README.md").read_text()
    docs_index = Path("docs/README.md").read_text()

    for path in (
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "docs/ARCHITECTURE.md",
        "docs/REVIEWER-GUIDE.md",
        "docs/AI-DEVELOPMENT.md",
    ):
        assert Path(path).is_file()

    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in Path("LICENSE").read_text()
    assert "REVIEWER-GUIDE.md" in readme
    assert "AI-DEVELOPMENT.md" in readme
    assert "ARCHITECTURE.md" in docs_index


def test_readme_local_links_resolve():
    text = Path("README.md").read_text()
    targets = re.findall(r"!?\[[^]]*\]\(([^)]+)\)", text)

    for raw_target in targets:
        target = raw_target.split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        assert Path(target).exists(), f"README local link does not exist: {raw_target}"


def test_peer_review_docs_local_links_resolve():
    documents = (
        Path("CONTRIBUTING.md"),
        Path("SECURITY.md"),
        Path("docs/ARCHITECTURE.md"),
        Path("docs/REVIEWER-GUIDE.md"),
        Path("docs/AI-DEVELOPMENT.md"),
    )

    for document in documents:
        targets = re.findall(r"!?\[[^]]*\]\(([^)]+)\)", document.read_text())
        for raw_target in targets:
            target = raw_target.split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = document.parent / target
            assert resolved.exists(), f"{document} local link does not exist: {raw_target}"


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
