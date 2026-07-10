"""Idempotent, audited native write commands for studio owners.

This is the deliberately narrow Milestone 4A mutation boundary. Every command
requires an exact ``studio_owner`` bearer with ``studio:write``, a session-bound
idempotency key, and (for edits) the current entity ETag. The business write,
append-only audit row, replay payload, and any deferred workflow marker commit in
one ``BEGIN IMMEDIATE`` transaction.

Money, contract, signature, and client-facing proposal decisions do not belong in
this router. They retain their existing reviewed web boundaries.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import hmac
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import audit, db, mobile_auth, pricing, workflows
from .admin import studio as admin_studio
from .mobile_owner_api import ProjectStatus

log = logging.getLogger("mise.mobile_owner_mutation_api")
router = APIRouter()

_INT64_MAX = 2**63 - 1
_IDEMPOTENCY_HEADER = "Idempotency-Key"
_IF_MATCH_HEADER = "If-Match"
_ACTOR = "mobile_owner"


class MobileWriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MobileWriteRequest(BaseModel):
    # JSON transports dates and enum values as strings; field validators still
    # enforce the exact bounded command vocabulary below.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClientCreate(MobileWriteRequest):
    name: str = Field(min_length=1, max_length=500)
    company: str | None = Field(default=None, max_length=500)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=20_000)
    usage_rights: str | None = Field(default=None, max_length=20_000)
    market: str = Field(default=pricing.DEFAULT_MARKET, min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name is required")
        return cleaned

    @field_validator("company", "email", "phone", "notes", "usage_rights")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else ""
        return cleaned or None

    @field_validator("market")
    @classmethod
    def clean_market(cls, value: str) -> str:
        return value.strip()


class ClientUpdate(ClientCreate):
    pass


class ClientDetail(MobileWriteModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    name: str = Field(min_length=1, max_length=500)
    company: str | None = Field(default=None, max_length=500)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=20_000)
    usage_rights: str | None = Field(default=None, max_length=20_000)
    market: str = Field(min_length=1, max_length=255)
    project_count: int = Field(ge=0)
    portal_published: bool
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)


class ProjectCreate(MobileWriteRequest):
    client_id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=1000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title is required")
        return cleaned


class ProjectUpdate(MobileWriteRequest):
    title: str = Field(min_length=1, max_length=1000)
    status: ProjectStatus
    notes: str | None = Field(default=None, max_length=20_000)
    shoot_on: dt.date | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title is required")
        return cleaned

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else ""
        return cleaned or None


class ProjectDetail(MobileWriteModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    client_id: int = Field(gt=0, le=_INT64_MAX)
    client_display_name: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    status: ProjectStatus
    notes: str | None = Field(default=None, max_length=20_000)
    gallery_id: int | None = Field(default=None, gt=0, le=_INT64_MAX)
    shoot_on: dt.date | None = None
    workspace_published: bool
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)


class TaskCreate(MobileWriteRequest):
    title: str = Field(min_length=1, max_length=1000)
    due_on: dt.date | None = None
    project_id: int | None = Field(default=None, gt=0, le=_INT64_MAX)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title is required")
        return cleaned


class TaskUpdate(TaskCreate):
    done: bool


class TaskDetail(MobileWriteModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=1000)
    due_on: dt.date | None = None
    project_id: int | None = Field(default=None, gt=0, le=_INT64_MAX)
    project_title: str | None = Field(default=None, min_length=1, max_length=1000)
    done: bool
    is_overdue: bool
    created_at: dt.datetime
    completed_at: dt.datetime | None = None

    @field_validator("created_at", "completed_at")
    @classmethod
    def timestamps_are_utc(cls, value: dt.datetime | None) -> dt.datetime | None:
        return _aware_utc(value) if value is not None else None


class TaskCollection(MobileWriteModel):
    items: list[TaskDetail] = Field(max_length=1000)


def require_studio_writer(request: Request) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(
        request,
        required_scopes=("studio:read", "studio:write"),
    )
    if principal.kind != mobile_auth.STUDIO_OWNER:
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "Studio owner write access is required.",
        )
    return principal


StudioWriter = Annotated[mobile_auth.Principal, Depends(require_studio_writer)]


@dataclass(frozen=True)
class CommandClaim:
    key: str
    operation: str
    request_sha256: str
    replay_json: str | None

    @property
    def replayed(self) -> bool:
        return self.replay_json is not None


@contextlib.contextmanager
def _immediate_transaction():
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        with contextlib.suppress(sqlite3.Error):
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def _idempotency_key(request: Request) -> str:
    supplied = request.headers.get(_IDEMPOTENCY_HEADER, "").strip()
    try:
        parsed = uuid.UUID(supplied)
    except (ValueError, AttributeError) as exc:
        raise mobile_auth.MobileAuthError(
            422,
            "request.idempotency_required",
            "A valid Idempotency-Key UUID is required for this command.",
        ) from exc
    return str(parsed)


def _claim_command(
    con: sqlite3.Connection,
    request: Request,
    principal: mobile_auth.Principal,
    operation: str,
    payload: dict,
) -> CommandClaim:
    key = _idempotency_key(request)
    canonical = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    row = con.execute(
        """SELECT operation, request_sha256, response_json
             FROM mobile_commands
            WHERE session_id=? AND idempotency_key=?""",
        (principal.session_id, key),
    ).fetchone()
    if row is not None:
        if row["operation"] != operation or not hmac.compare_digest(row["request_sha256"], digest):
            raise mobile_auth.MobileAuthError(
                409,
                "request.idempotency_conflict",
                "This idempotency key was already used for a different command.",
            )
        return CommandClaim(key, operation, digest, str(row["response_json"]))
    return CommandClaim(key, operation, digest, None)


def _finish_command(
    con: sqlite3.Connection,
    principal: mobile_auth.Principal,
    claim: CommandClaim,
    value: MobileWriteModel,
    *,
    status_code: int,
    effect: dict | None = None,
) -> None:
    con.execute(
        """INSERT INTO mobile_commands
           (session_id, idempotency_key, operation, request_sha256,
            status_code, response_json, effect_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            principal.session_id,
            claim.key,
            claim.operation,
            claim.request_sha256,
            status_code,
            value.model_dump_json(),
            (
                json.dumps(effect, sort_keys=True, separators=(",", ":"))
                if effect is not None
                else None
            ),
        ),
    )


def _complete_pending_effect(principal: mobile_auth.Principal, key: str) -> None:
    row = db.one(
        """SELECT effect_json, effects_completed_at
             FROM mobile_commands
            WHERE session_id=? AND idempotency_key=?""",
        (principal.session_id, key),
    )
    if row is None or row["effect_json"] is None or row["effects_completed_at"] is not None:
        return
    try:
        effect = json.loads(row["effect_json"])
        if effect.get("kind") != "project_status":
            raise ValueError("unsupported mobile command effect")
        project_id = int(effect["project_id"])
        status = str(effect["status"])
        workflows.fire_workflow(f"status:{status}", project_id)
    except Exception:
        log.exception("mobile command effect failed for session command %s", key)
        return
    db.run(
        """UPDATE mobile_commands SET effects_completed_at=datetime('now')
            WHERE session_id=? AND idempotency_key=? AND effects_completed_at IS NULL""",
        (principal.session_id, key),
    )


def _replay(claim: CommandClaim, model: type[MobileWriteModel]) -> MobileWriteModel:
    assert claim.replay_json is not None
    return model.model_validate_json(claim.replay_json)


def _request_payload(body: BaseModel, request: Request, *, include_match: bool) -> dict:
    payload = body.model_dump(mode="json")
    if include_match:
        payload["if_match"] = request.headers.get(_IF_MATCH_HEADER, "").strip()
    return payload


def _aware_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include an offset")
    return value.astimezone(dt.UTC)


def _sqlite_utc(value: object | None) -> dt.datetime | None:
    if value is None:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _date(value: object | None) -> dt.date | None:
    if value is None or not str(value).strip():
        return None
    return dt.date.fromisoformat(str(value)[:10])


def _optional(value: object | None, maximum: int) -> str | None:
    cleaned = str(value).strip()[:maximum] if value is not None else ""
    return cleaned or None


def _etag(kind: str, value: MobileWriteModel) -> str:
    digest = hashlib.sha256(value.model_dump_json().encode()).hexdigest()
    return f'"{kind}-{digest[:32]}"'


def _etag_matches(supplied: str | None, current: str) -> bool:
    if not supplied:
        return False
    for candidate in supplied.split(","):
        value = candidate.strip()
        if value == "*":
            return True
        if value.startswith("W/"):
            value = value[2:].strip()
        if value == current:
            return True
    return False


def _strong_etag_matches(supplied: str, current: str) -> bool:
    return any(candidate.strip() == current for candidate in supplied.split(","))


def _require_current(request: Request, current: str) -> None:
    supplied = request.headers.get(_IF_MATCH_HEADER)
    if not supplied:
        raise mobile_auth.MobileAuthError(
            422,
            "resource.if_match_required",
            "Reload this item before saving changes.",
        )
    if not _strong_etag_matches(supplied, current):
        raise mobile_auth.MobileAuthError(
            409,
            "resource.version_conflict",
            "This item changed on another device. Reload it and review your changes.",
        )


def _private(response: Response, *, etag: str | None = None, replayed: bool = False) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    if etag is not None:
        response.headers["ETag"] = etag
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"


def _client_detail(con: sqlite3.Connection, client_id: int) -> ClientDetail:
    row = con.execute(
        """SELECT c.id, c.name, c.company, c.email, c.phone, c.notes,
                  c.usage_rights, c.market, c.created_at,
                  (SELECT COUNT(*) FROM projects p WHERE p.client_id=c.id) AS project_count,
                  EXISTS(SELECT 1 FROM portals po
                         WHERE po.client_id=c.id AND po.published=1) AS portal_published
             FROM clients c WHERE c.id=?""",
        (client_id,),
    ).fetchone()
    if row is None:
        raise mobile_auth.MobileAuthError(404, "client.not_found", "Client not found.")
    return ClientDetail(
        id=int(row["id"]),
        name=str(row["name"]).strip()[:500] or f"Client {row['id']}",
        company=_optional(row["company"], 500),
        email=_optional(row["email"], 320),
        phone=_optional(row["phone"], 100),
        notes=_optional(row["notes"], 20_000),
        usage_rights=_optional(row["usage_rights"], 20_000),
        market=str(row["market"] or pricing.DEFAULT_MARKET)[:255],
        project_count=max(0, int(row["project_count"])),
        portal_published=bool(row["portal_published"]),
        created_at=_sqlite_utc(row["created_at"]),
    )


def _project_detail(con: sqlite3.Connection, project_id: int) -> ProjectDetail:
    row = con.execute(
        """SELECT p.id, p.client_id, p.title, p.status, p.notes, p.gallery_id,
                  p.shoot_date, p.workspace_published, p.created_at,
                  COALESCE(NULLIF(c.company, ''), c.name) AS client_display_name
             FROM projects p JOIN clients c ON c.id=p.client_id
            WHERE p.id=?""",
        (project_id,),
    ).fetchone()
    if row is None:
        raise mobile_auth.MobileAuthError(404, "project.not_found", "Project not found.")
    return ProjectDetail(
        id=int(row["id"]),
        client_id=int(row["client_id"]),
        client_display_name=(
            str(row["client_display_name"]).strip()[:500] or f"Client {row['client_id']}"
        ),
        title=str(row["title"]).strip()[:1000] or f"Project {row['id']}",
        status=ProjectStatus(str(row["status"])),
        notes=_optional(row["notes"], 20_000),
        gallery_id=int(row["gallery_id"]) if row["gallery_id"] is not None else None,
        shoot_on=_date(row["shoot_date"]),
        workspace_published=bool(row["workspace_published"]),
        created_at=_sqlite_utc(row["created_at"]),
    )


def _task_detail(con: sqlite3.Connection, task_id: int) -> TaskDetail:
    row = con.execute(
        """SELECT t.id, t.title, t.due_date, t.done, t.project_id,
                  t.created_at, t.done_at, p.title AS project_title
             FROM tasks t LEFT JOIN projects p ON p.id=t.project_id
            WHERE t.id=?""",
        (task_id,),
    ).fetchone()
    if row is None:
        raise mobile_auth.MobileAuthError(404, "task.not_found", "Task not found.")
    due_on = _date(row["due_date"])
    return TaskDetail(
        id=int(row["id"]),
        title=str(row["title"]).strip()[:1000] or f"Task {row['id']}",
        due_on=due_on,
        project_id=int(row["project_id"]) if row["project_id"] is not None else None,
        project_title=_optional(row["project_title"], 1000),
        done=bool(row["done"]),
        is_overdue=(
            not bool(row["done"]) and due_on is not None and due_on < admin_studio._today()
        ),
        created_at=_sqlite_utc(row["created_at"]),
        completed_at=_sqlite_utc(row["done_at"]),
    )


def _validate_market(market: str) -> None:
    if market not in pricing.MARKETS:
        raise mobile_auth.MobileAuthError(422, "client.invalid_market", "Unknown client market.")


def _validate_project(con: sqlite3.Connection, project_id: int | None) -> None:
    if (
        project_id is not None
        and con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None
    ):
        raise mobile_auth.MobileAuthError(422, "task.invalid_project", "Project not found.")


def _diff(before: MobileWriteModel, after: MobileWriteModel, fields: tuple[str, ...]) -> dict:
    old = before.model_dump(mode="json")
    new = after.model_dump(mode="json")
    return {field: [old[field], new[field]] for field in fields if old[field] != new[field]}


@router.get("/clients/{client_id}", response_model=ClientDetail, tags=["owner mutations"])
def client_detail(
    request: Request,
    response: Response,
    client_id: Annotated[int, Path(ge=1)],
    _principal: StudioWriter,
) -> ClientDetail | Response:
    con = db.connect()
    try:
        value = _client_detail(con, client_id)
    finally:
        con.close()
    etag = _etag("client", value)
    if _etag_matches(request.headers.get("If-None-Match"), etag):
        return Response(status_code=304, headers={"Cache-Control": "no-store", "ETag": etag})
    _private(response, etag=etag)
    return value


@router.post(
    "/clients",
    response_model=ClientDetail,
    status_code=201,
    tags=["owner mutations"],
)
def create_client(
    request: Request,
    response: Response,
    body: ClientCreate,
    principal: StudioWriter,
) -> ClientDetail:
    _validate_market(body.market)
    with _immediate_transaction() as con:
        claim = _claim_command(
            con, request, principal, "client.create", body.model_dump(mode="json")
        )
        if claim.replayed:
            value = _replay(claim, ClientDetail)
        else:
            client_id = con.execute(
                """INSERT INTO clients
                   (name, company, email, phone, notes, usage_rights, market)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    body.name,
                    body.company,
                    body.email,
                    body.phone,
                    body.notes,
                    body.usage_rights,
                    body.market,
                ),
            ).lastrowid
            value = _client_detail(con, int(client_id))
            audit.log(
                con,
                "client",
                client_id,
                "create",
                actor=_ACTOR,
                diff={"name": body.name, "company": body.company, "market": body.market},
            )
            _finish_command(con, principal, claim, value, status_code=201)
    _private(response, etag=_etag("client", value), replayed=claim.replayed)
    return value


@router.patch("/clients/{client_id}", response_model=ClientDetail, tags=["owner mutations"])
def update_client(
    request: Request,
    response: Response,
    body: ClientUpdate,
    principal: StudioWriter,
    client_id: Annotated[int, Path(ge=1)],
) -> ClientDetail:
    _validate_market(body.market)
    with _immediate_transaction() as con:
        claim = _claim_command(
            con,
            request,
            principal,
            f"client.update:{client_id}",
            _request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = _replay(claim, ClientDetail)
        else:
            before = _client_detail(con, client_id)
            _require_current(request, _etag("client", before))
            con.execute(
                """UPDATE clients SET name=?, company=?, email=?, phone=?, notes=?,
                          usage_rights=?, market=? WHERE id=?""",
                (
                    body.name,
                    body.company,
                    body.email,
                    body.phone,
                    body.notes,
                    body.usage_rights,
                    body.market,
                    client_id,
                ),
            )
            value = _client_detail(con, client_id)
            changes = _diff(
                before,
                value,
                ("name", "company", "email", "phone", "notes", "usage_rights", "market"),
            )
            if changes:
                audit.log(con, "client", client_id, "update", actor=_ACTOR, diff=changes)
            _finish_command(con, principal, claim, value, status_code=200)
    _private(response, etag=_etag("client", value), replayed=claim.replayed)
    return value


@router.get("/projects/{project_id}", response_model=ProjectDetail, tags=["owner mutations"])
def project_detail(
    request: Request,
    response: Response,
    project_id: Annotated[int, Path(ge=1)],
    _principal: StudioWriter,
) -> ProjectDetail | Response:
    con = db.connect()
    try:
        value = _project_detail(con, project_id)
    finally:
        con.close()
    etag = _etag("project", value)
    if _etag_matches(request.headers.get("If-None-Match"), etag):
        return Response(status_code=304, headers={"Cache-Control": "no-store", "ETag": etag})
    _private(response, etag=etag)
    return value


@router.post(
    "/projects",
    response_model=ProjectDetail,
    status_code=201,
    tags=["owner mutations"],
)
def create_project(
    request: Request,
    response: Response,
    body: ProjectCreate,
    principal: StudioWriter,
) -> ProjectDetail:
    with _immediate_transaction() as con:
        claim = _claim_command(
            con, request, principal, "project.create", body.model_dump(mode="json")
        )
        if claim.replayed:
            value = _replay(claim, ProjectDetail)
        else:
            if (
                con.execute("SELECT 1 FROM clients WHERE id=?", (body.client_id,)).fetchone()
                is None
            ):
                raise mobile_auth.MobileAuthError(
                    422, "project.invalid_client", "Client not found."
                )
            project_id = con.execute(
                "INSERT INTO projects (client_id, title) VALUES (?,?)",
                (body.client_id, body.title),
            ).lastrowid
            value = _project_detail(con, int(project_id))
            audit.log(
                con,
                "project",
                project_id,
                "create",
                actor=_ACTOR,
                diff={"client_id": body.client_id, "title": body.title},
            )
            _finish_command(con, principal, claim, value, status_code=201)
    _private(response, etag=_etag("project", value), replayed=claim.replayed)
    return value


@router.patch("/projects/{project_id}", response_model=ProjectDetail, tags=["owner mutations"])
def update_project(
    request: Request,
    response: Response,
    body: ProjectUpdate,
    principal: StudioWriter,
    project_id: Annotated[int, Path(ge=1)],
) -> ProjectDetail:
    if body.status.value not in admin_studio.PROJECT_STATUSES:
        raise mobile_auth.MobileAuthError(422, "project.invalid_status", "Unknown project status.")
    effect = None
    with _immediate_transaction() as con:
        claim = _claim_command(
            con,
            request,
            principal,
            f"project.update:{project_id}",
            _request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = _replay(claim, ProjectDetail)
        else:
            before = _project_detail(con, project_id)
            _require_current(request, _etag("project", before))
            con.execute(
                """UPDATE projects SET title=?, status=?, notes=?, shoot_date=?,
                          stage_changed_at=CASE WHEN status=? THEN stage_changed_at
                                                ELSE datetime('now') END
                    WHERE id=?""",
                (
                    body.title,
                    body.status.value,
                    body.notes,
                    body.shoot_on.isoformat() if body.shoot_on is not None else None,
                    body.status.value,
                    project_id,
                ),
            )
            value = _project_detail(con, project_id)
            changes = _diff(before, value, ("title", "status", "notes", "shoot_on"))
            if changes:
                audit.log(con, "project", project_id, "update", actor=_ACTOR, diff=changes)
            if before.status != value.status:
                effect = {
                    "kind": "project_status",
                    "project_id": project_id,
                    "status": value.status.value,
                }
            _finish_command(con, principal, claim, value, status_code=200, effect=effect)
    _complete_pending_effect(principal, claim.key)
    _private(response, etag=_etag("project", value), replayed=claim.replayed)
    return value


@router.get("/tasks", response_model=TaskCollection, tags=["owner mutations"])
def tasks(response: Response, _principal: StudioWriter) -> TaskCollection:
    con = db.connect()
    try:
        rows = con.execute(
            """SELECT id FROM tasks
                ORDER BY done, due_date IS NULL, due_date, id DESC LIMIT 1000"""
        ).fetchall()
        value = TaskCollection(items=[_task_detail(con, int(row["id"])) for row in rows])
    finally:
        con.close()
    _private(response)
    return value


@router.get("/tasks/{task_id}", response_model=TaskDetail, tags=["owner mutations"])
def task_detail(
    request: Request,
    response: Response,
    task_id: Annotated[int, Path(ge=1)],
    _principal: StudioWriter,
) -> TaskDetail | Response:
    con = db.connect()
    try:
        value = _task_detail(con, task_id)
    finally:
        con.close()
    etag = _etag("task", value)
    if _etag_matches(request.headers.get("If-None-Match"), etag):
        return Response(status_code=304, headers={"Cache-Control": "no-store", "ETag": etag})
    _private(response, etag=etag)
    return value


@router.post("/tasks", response_model=TaskDetail, status_code=201, tags=["owner mutations"])
def create_task(
    request: Request,
    response: Response,
    body: TaskCreate,
    principal: StudioWriter,
) -> TaskDetail:
    with _immediate_transaction() as con:
        claim = _claim_command(con, request, principal, "task.create", body.model_dump(mode="json"))
        if claim.replayed:
            value = _replay(claim, TaskDetail)
        else:
            _validate_project(con, body.project_id)
            task_id = con.execute(
                "INSERT INTO tasks (title, due_date, project_id) VALUES (?,?,?)",
                (
                    body.title,
                    body.due_on.isoformat() if body.due_on is not None else None,
                    body.project_id,
                ),
            ).lastrowid
            value = _task_detail(con, int(task_id))
            audit.log(
                con,
                "task",
                task_id,
                "create",
                actor=_ACTOR,
                diff={
                    "title": body.title,
                    "due_on": body.due_on,
                    "project_id": body.project_id,
                },
            )
            _finish_command(con, principal, claim, value, status_code=201)
    _private(response, etag=_etag("task", value), replayed=claim.replayed)
    return value


@router.patch("/tasks/{task_id}", response_model=TaskDetail, tags=["owner mutations"])
def update_task(
    request: Request,
    response: Response,
    body: TaskUpdate,
    principal: StudioWriter,
    task_id: Annotated[int, Path(ge=1)],
) -> TaskDetail:
    with _immediate_transaction() as con:
        claim = _claim_command(
            con,
            request,
            principal,
            f"task.update:{task_id}",
            _request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = _replay(claim, TaskDetail)
        else:
            before = _task_detail(con, task_id)
            _require_current(request, _etag("task", before))
            _validate_project(con, body.project_id)
            con.execute(
                """UPDATE tasks SET title=?, due_date=?, project_id=?, done=?,
                          done_at=CASE WHEN ?=1 AND done=0 THEN datetime('now')
                                       WHEN ?=0 THEN NULL ELSE done_at END
                    WHERE id=?""",
                (
                    body.title,
                    body.due_on.isoformat() if body.due_on is not None else None,
                    body.project_id,
                    1 if body.done else 0,
                    1 if body.done else 0,
                    1 if body.done else 0,
                    task_id,
                ),
            )
            value = _task_detail(con, task_id)
            changes = _diff(before, value, ("title", "due_on", "project_id", "done"))
            if changes:
                audit.log(con, "task", task_id, "update", actor=_ACTOR, diff=changes)
            _finish_command(con, principal, claim, value, status_code=200)
    _private(response, etag=_etag("task", value), replayed=claim.replayed)
    return value


@router.delete("/tasks/{task_id}", response_model=TaskDetail, tags=["owner mutations"])
def delete_task(
    request: Request,
    response: Response,
    principal: StudioWriter,
    task_id: Annotated[int, Path(ge=1)],
) -> TaskDetail:
    payload = {"if_match": request.headers.get(_IF_MATCH_HEADER, "").strip()}
    with _immediate_transaction() as con:
        claim = _claim_command(con, request, principal, f"task.delete:{task_id}", payload)
        if claim.replayed:
            value = _replay(claim, TaskDetail)
        else:
            value = _task_detail(con, task_id)
            _require_current(request, _etag("task", value))
            con.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            audit.log(
                con,
                "task",
                task_id,
                "delete",
                actor=_ACTOR,
                diff={"title": value.title, "project_id": value.project_id},
            )
            _finish_command(con, principal, claim, value, status_code=200)
    _private(response, replayed=claim.replayed)
    return value
