"""Small CRM helpers for tags and project custom fields."""

from __future__ import annotations

from . import db


def upsert_project_field(project_id: int, field_key: str, field_value: str) -> None:
    key = field_key.strip()
    value = field_value.strip()
    if not key or not value:
        raise ValueError("field key and value are required")
    db.run(
        """INSERT INTO project_custom_fields (project_id, field_key, field_value)
           VALUES (?,?,?)
           ON CONFLICT(project_id, field_key) DO UPDATE SET
             field_value=excluded.field_value,
             updated_at=datetime('now')""",
        (project_id, key, value),
    )


def ensure_tag(name: str, color: str = "#2f5c45") -> int:
    tag_name = name.strip()
    if not tag_name:
        raise ValueError("tag name is required")
    db.run(
        "INSERT OR IGNORE INTO tags (name, color) VALUES (?,?)",
        (tag_name, color.strip() or "#2f5c45"),
    )
    return db.one("SELECT id FROM tags WHERE name=?", (tag_name,))["id"]


def assign_client_tag(client_id: int, tag_id: int) -> None:
    db.run(
        "INSERT OR IGNORE INTO client_tags (client_id, tag_id) VALUES (?,?)",
        (client_id, tag_id),
    )
