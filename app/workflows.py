"""Tiny workflow engine for the hosted-studio product layer.

Rules intentionally create ordinary tasks plus project timeline events. That
keeps automation visible and editable instead of hiding work in a queue.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import db


def _dedupe_key(
    trigger_key: str,
    project_id: int,
    rule_id: int,
    ref_kind: str | None,
    ref_id: int | None,
) -> str:
    return ":".join(
        [
            trigger_key,
            str(project_id),
            str(rule_id),
            ref_kind or "-",
            str(ref_id or 0),
        ]
    )


def fire_workflow(
    trigger_key: str,
    project_id: int,
    *,
    ref_kind: str | None = None,
    ref_id: int | None = None,
) -> int:
    """Run active rules for ``trigger_key`` against a project.

    Returns the number of new visible actions created. Re-firing the same rule
    for the same project/reference is idempotent through project_events.dedupe_key.
    """
    rules = db.all_(
        """SELECT * FROM workflow_rules
           WHERE trigger_key=? AND active=1
           ORDER BY delay_days, id""",
        (trigger_key,),
    )
    created = 0
    for rule in rules:
        key = _dedupe_key(trigger_key, project_id, rule["id"], ref_kind, ref_id)
        due_date = (date.today() + timedelta(days=rule["delay_days"])).isoformat()
        with db.tx() as con:
            if con.execute("SELECT 1 FROM project_events WHERE dedupe_key=?", (key,)).fetchone():
                continue
            task_id = None
            if rule["action_key"] == "task":
                task_id = con.execute(
                    "INSERT INTO tasks (title, due_date, project_id) VALUES (?,?,?)",
                    (rule["task_title"], due_date, project_id),
                ).lastrowid
            con.execute(
                """INSERT INTO project_events
                   (project_id, kind, label, ref_kind, ref_id, due_date,
                    workflow_rule_id, dedupe_key)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    project_id,
                    "task" if task_id else "event",
                    rule["task_title"],
                    "task" if task_id else ref_kind,
                    task_id if task_id else ref_id,
                    due_date,
                    rule["id"],
                    key,
                ),
            )
        created += 1
    return created


def record_project_event(
    project_id: int,
    kind: str,
    label: str,
    *,
    ref_kind: str | None = None,
    ref_id: int | None = None,
    dedupe_key: str | None = None,
) -> int | None:
    """Append one project timeline event, optionally idempotent."""
    try:
        return db.run(
            """INSERT INTO project_events
               (project_id, kind, label, ref_kind, ref_id, dedupe_key)
               VALUES (?,?,?,?,?,?)""",
            (project_id, kind, label, ref_kind, ref_id, dedupe_key),
        )
    except db.sqlite3.IntegrityError:
        return None
