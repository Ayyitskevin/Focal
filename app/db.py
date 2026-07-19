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


class ExistingDatabaseUnavailable(sqlite3.OperationalError):
    """An existing-only connection could not open or validate its database."""


class _ExistingFailureState:
    """Mutable so a Starlette child task can signal its parent middleware task."""

    def __init__(self):
        self.failure: ExistingDatabaseUnavailable | None = None


_DB_EXISTING_FAILURE_CTX: ContextVar[_ExistingFailureState | None] = ContextVar(
    "mise_db_existing_failure", default=None
)


_STORAGE_UNAVAILABLE_CODES = frozenset(
    getattr(sqlite3, name)
    for name in (
        "SQLITE_CANTOPEN",
        "SQLITE_CORRUPT",
        "SQLITE_IOERR",
        "SQLITE_NOTADB",
        "SQLITE_READONLY",
    )
    if hasattr(sqlite3, name)
)
_STORAGE_UNAVAILABLE_MESSAGES = (
    "database disk image is malformed",
    "file is not a database",
    "unable to open database file",
)


def _is_storage_unavailable_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.Error):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in _STORAGE_UNAVAILABLE_CODES
    message = str(exc).lower()
    return any(fragment in message for fragment in _STORAGE_UNAVAILABLE_MESSAGES)


def _translate_storage_call(function, /, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except sqlite3.Error as exc:
        if _is_storage_unavailable_error(exc):
            failure = ExistingDatabaseUnavailable("existing database unavailable")
            _remember_existing_failure(failure)
            raise failure from exc
        raise


def _remember_existing_failure(failure: ExistingDatabaseUnavailable) -> None:
    state = _DB_EXISTING_FAILURE_CTX.get()
    if state is not None:
        state.failure = failure


class ExistingOnlyCursor(sqlite3.Cursor):
    """Cursor that turns only SQLite storage codes into the typed sentinel."""

    def execute(self, sql, parameters=()):
        return _translate_storage_call(super().execute, sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return _translate_storage_call(super().executemany, sql, seq_of_parameters)

    def executescript(self, sql_script):
        return _translate_storage_call(super().executescript, sql_script)

    def fetchone(self):
        return _translate_storage_call(super().fetchone)

    def fetchmany(self, size=None):
        if size is None:
            return _translate_storage_call(super().fetchmany)
        return _translate_storage_call(super().fetchmany, size)

    def fetchall(self):
        return _translate_storage_call(super().fetchall)

    def __next__(self):
        return _translate_storage_call(super().__next__)


class ExistingOnlyConnection(sqlite3.Connection):
    """Write-capable existing DB connection whose I/O failures remain observable."""

    def cursor(self, factory=None):
        return super().cursor(factory or ExistingOnlyCursor)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        return self.cursor().executescript(sql_script)

    def commit(self):
        return _translate_storage_call(super().commit)

    def rollback(self):
        return _translate_storage_call(super().rollback)

    def backup(self, target, *, pages=-1, progress=None, name="main", sleep=0.250):
        return _translate_storage_call(
            super().backup,
            target,
            pages=pages,
            progress=progress,
            name=name,
            sleep=sleep,
        )

    def __exit__(self, exc_type, exc_value, traceback):
        return _translate_storage_call(super().__exit__, exc_type, exc_value, traceback)


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


def set_existing_failure_boundary():
    return _DB_EXISTING_FAILURE_CTX.set(_ExistingFailureState())


def reset_existing_failure_boundary(token) -> None:
    _DB_EXISTING_FAILURE_CTX.reset(token)


def existing_database_failure() -> ExistingDatabaseUnavailable | None:
    state = _DB_EXISTING_FAILURE_CTX.get()
    return state.failure if state is not None else None


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else current_db_path()
    existing_only = _DB_EXISTING_ONLY_CTX.get()
    con: sqlite3.Connection | None = None
    try:
        if existing_only:
            if failure := existing_database_failure():
                raise failure
            # SQLite's normal path mode creates a missing database. Existing tenant
            # runtimes must also prove this is a provisioned Mise DB before any
            # write-capable PRAGMA can initialize a zero-byte or unrelated file.
            uri = f"{db_path.resolve().as_uri()}?mode=rw"
            con = sqlite3.connect(uri, timeout=30, uri=True, factory=ExistingOnlyConnection)
            marker_table = con.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            marker = (
                con.execute("SELECT 1 FROM schema_migrations WHERE name='001_init.sql'").fetchone()
                if marker_table
                else None
            )
            if marker is None:
                failure = ExistingDatabaseUnavailable(
                    "existing database is not a provisioned Mise tenant database"
                )
                _remember_existing_failure(failure)
                raise failure
            con.row_factory = sqlite3.Row
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(db_path, timeout=30)
            con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        return con
    except ExistingDatabaseUnavailable:
        if con is not None:
            con.close()
        raise
    except sqlite3.Error as exc:
        if con is not None:
            con.close()
        if existing_only and _is_storage_unavailable_error(exc):
            failure = ExistingDatabaseUnavailable("existing database unavailable")
            _remember_existing_failure(failure)
            raise failure from exc
        raise


def migrate(path: Path | None = None) -> None:
    # Provisioning may create its directory tree. An existing-only migration runs
    # only against a positively opened tenant DB and must never recreate storage.
    if not _DB_EXISTING_ONLY_CTX.get():
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
