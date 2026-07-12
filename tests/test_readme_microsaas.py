from pathlib import Path


def test_readme_keeps_flat_hosted_positioning():
    text = Path("README.md").read_text()

    required = [
        "Exactly **$20/month**",
        "No paid tiers",
        "14-day trial",
        "MISE_RCLONE_CONFIG_PATH",
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
        "manifest-committed backup",
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
        "manifest-committed backup",
        "scripts/launch-hosted-production.sh",
    ]
    for phrase in required:
        assert phrase in text
    assert "docker compose" in script
    assert "python scripts/hosted-preflight.py" in script


def test_hosted_launch_gates_current_images_before_public_ingress():
    script = Path("scripts/launch-hosted-production.sh").read_text()

    build = script.index("compose build mise backup")
    static = script.index(
        "compose run --rm --no-deps mise python scripts/hosted-preflight.py --static"
    )
    stop = script.index("compose stop caddy backup")
    app = script.index("compose up -d mise")
    forced_backup = script.index(
        "compose run --rm --no-deps --entrypoint python backup scripts/hosted-backup.py"
    )
    runtime = script.index("compose exec -T mise python scripts/hosted-preflight.py")
    public = script.index("compose up -d backup caddy")

    assert build < static < stop < app < forced_backup < runtime < public
    assert "\npython scripts/hosted-preflight.py --static\n" not in script
    assert "compose up --build -d mise backup" not in script
