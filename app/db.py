"""SQLite access — WAL mode, short-lived connections (safe across job threads)."""

import re
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
_DB_EXISTING_ONLY_CTX: ContextVar[bool] = ContextVar("mise_db_existing_only", default=False)


def current_db_path() -> Path:
    return _DB_PATH_CTX.get() or Path(config.DB_PATH)


def set_request_db_path(path: Path):
    return _DB_PATH_CTX.set(Path(path))


def reset_request_db_path(token) -> None:
    _DB_PATH_CTX.reset(token)


def set_existing_only(value: bool = True):
    """Require connections in this context to open an existing DB read/write."""
    return _DB_EXISTING_ONLY_CTX.set(value)


def reset_existing_only(token) -> None:
    _DB_EXISTING_ONLY_CTX.reset(token)


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else current_db_path()
    if _DB_EXISTING_ONLY_CTX.get():
        # SQLite's normal path mode creates a missing database. Background
        # retention workers must fail instead if deletion races their sweep.
        uri = f"{db_path.resolve().as_uri()}?mode=rw"
        con = sqlite3.connect(uri, timeout=30, uri=True)
    else:
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
            _apply_migration(con, path)
    finally:
        con.close()


# A migration that manages its own transaction (top-level COMMIT;, e.g. 031's
# BEGIN/COMMIT backfill) is already all-or-nothing; a trigger body ends in END;,
# not COMMIT;, so this only matches real transaction control.
_SELF_TXN = re.compile(r"(?im)^\s*commit\s*;")


def _apply_migration(con: sqlite3.Connection, path: Path) -> None:
    """Apply one migration file and record it — atomically where we can.

    A file with no transaction control (the common case: plain DDL/DML) is wrapped
    with its schema_migrations marker in a SINGLE transaction, so an interrupted
    apply (crash / OOM / deploy restart mid-file) rolls back ENTIRELY and re-runs
    cleanly next boot. Without this, a half-applied non-idempotent migration bricks
    the DB: SQLite has no DROP COLUMN IF EXISTS, so re-running e.g. 075's bare
    DROP COLUMNs after a partial apply throws on the first already-dropped column
    and migrate() can never complete for that database.

    A file that already carries its own BEGIN/COMMIT is self-atomic; we run it as-is
    and record it in a follow-up commit (executescript can't be nested in a txn).
    """
    script = path.read_text()
    name = path.name
    assert "'" not in name  # our own filenames; inlined below since executescript can't bind
    if _SELF_TXN.search(script):
        con.executescript(script)
        con.execute("INSERT INTO schema_migrations (name) VALUES (?)", (name,))
        con.commit()
    else:
        con.executescript(
            f"BEGIN;\n{script}\nINSERT INTO schema_migrations (name) VALUES ('{name}');\nCOMMIT;"
        )


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
