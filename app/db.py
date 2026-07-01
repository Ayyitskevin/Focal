"""SQLite access — WAL mode, short-lived connections (safe across job threads)."""

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from fastapi import HTTPException

from . import config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_ALIASES = {
    # Flow briefly applied the Plutus gallery columns under this later filename.
    # Treat both names as equivalent so a clean GitHub deploy does not re-run
    # the same ALTER TABLE statements against production.
    "055_plutus_upsell.sql": {"058_plutus_upsell.sql"},
    "058_plutus_upsell.sql": {"055_plutus_upsell.sql"},
}
_DB_PATH_CTX: ContextVar[Path | None] = ContextVar("mise_db_path", default=None)


def current_db_path() -> Path:
    return _DB_PATH_CTX.get() or Path(config.DB_PATH)


def set_request_db_path(path: Path):
    return _DB_PATH_CTX.set(Path(path))


def reset_request_db_path(token) -> None:
    _DB_PATH_CTX.reset(token)


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else current_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def migrate(path: Path | None = None) -> None:
    config.ensure_dirs()
    con = connect(path)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                       name TEXT PRIMARY KEY,
                       applied_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        applied = {r["name"] for r in con.execute("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            aliases = MIGRATION_ALIASES.get(path.name, set())
            if path.name in applied or aliases.intersection(applied):
                continue
            con.executescript(path.read_text())
            con.execute("INSERT INTO schema_migrations (name) VALUES (?)", (path.name,))
            con.commit()
    finally:
        con.close()


def ident(name: str, allowed) -> str:
    """Gate a SQL identifier (table/column) that gets interpolated into a query
    string. Values always go through `?` placeholders; identifiers can't, so any
    interpolated name must be checked against an allowlist HERE, at the point of
    use. Raises if `name` isn't allowed — a careless edit fails loud instead of
    becoming injection (R12)."""
    if name not in allowed:
        raise ValueError(f"disallowed SQL identifier: {name!r}")
    return name


def one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    con = connect()
    try:
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


def all_(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    con = connect()
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def run(sql: str, params: tuple = ()) -> int:
    """Execute and commit; returns lastrowid."""
    con = connect()
    try:
        cur = con.execute(sql, params)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def get_or_404(sql: str, params: tuple = (), *, detail: str = "Not found") -> sqlite3.Row:
    """Convenience wrapper: one() + 404 if missing.

    Reduces the repeated get_* + 404 boilerplate across admin modules.
    Use for simple ID lookups; complex JOIN queries can stay in place or use
    this with their full SELECT.
    """
    row = one(sql, params)
    if row is None:
        raise HTTPException(status_code=404, detail=detail)
    return row


def clients_for_select() -> list[sqlite3.Row]:
    """Lightweight list for admin <select> dropdowns (id, name, company)."""
    return all_("SELECT id, name, company FROM clients ORDER BY name")


@contextmanager
def tx():
    """Atomic unit of work: commit on clean exit, rollback on exception.

    Use when multiple writes must land together (e.g. a soft-delete and its
    audit_log row). The caller runs statements on the yielded connection.
    """
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
