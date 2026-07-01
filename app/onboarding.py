"""Hosted onboarding checklist derived from real studio state."""

from __future__ import annotations

from . import config, db

ADMIN_ONBOARDING_PATH = "/admin/onboarding"


def _count(sql: str, params: tuple = ()) -> int:
    return int(db.one(sql, params)["n"])


def setup_status() -> dict:
    counts = {
        "packages": _count("SELECT COUNT(*) AS n FROM packages WHERE active=1"),
        "workflow_rules": _count("SELECT COUNT(*) AS n FROM workflow_rules WHERE active=1"),
        "forms": _count("SELECT COUNT(*) AS n FROM forms WHERE active=1"),
        "clients": _count("SELECT COUNT(*) AS n FROM clients"),
        "projects": _count("SELECT COUNT(*) AS n FROM projects"),
        "galleries": _count("SELECT COUNT(*) AS n FROM galleries WHERE published=1"),
        "workspaces": _count("SELECT COUNT(*) AS n FROM projects WHERE workspace_published=1"),
    }
    steps = [
        {
            "key": "niche",
            "label": "Install a niche preset",
            "detail": "Seeds packages, workflow rules, forms, and CRM tags.",
            "done": counts["packages"] > 0 and counts["workflow_rules"] > 0,
            "href": "/admin/studio/automation",
        },
        {
            "key": "lead",
            "label": "Publish a lead path",
            "detail": "Create at least one package page or active lead form.",
            "done": counts["packages"] > 0 or counts["forms"] > 0,
            "href": "/admin/studio/automation",
        },
        {
            "key": "project",
            "label": "Add a client project",
            "detail": "Demo data is fine while learning the workflow.",
            "done": counts["clients"] > 0 and counts["projects"] > 0,
            "href": "/admin/studio",
        },
        {
            "key": "delivery",
            "label": "Publish a client delivery surface",
            "detail": "A live gallery or workspace proves the client experience.",
            "done": counts["galleries"] > 0 or counts["workspaces"] > 0,
            "href": "/admin/galleries",
        },
    ]
    done = sum(1 for step in steps if step["done"])
    return {
        "counts": counts,
        "steps": steps,
        "done": done,
        "total": len(steps),
        "complete": done == len(steps),
    }


def first_admin_destination(default: str = "/admin/home") -> str:
    """Return the first useful hosted admin destination for the current tenant."""
    if not config.SAAS_MODE:
        return default
    from . import saas

    if not saas.current_tenant():
        return default
    return default if setup_status()["complete"] else ADMIN_ONBOARDING_PATH
