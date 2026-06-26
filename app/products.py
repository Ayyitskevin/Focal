"""Aphrodite product-image foundation — the deterministic guards under a render capability.

Product-image generation (Aphrodite) is the last sidecar capability and the most spend- and
rights-sensitive: it *generates* imagery, which costs money and raises copyright/consent
questions (audit §13.5). So — exactly as the audit insists — the model only ever *proposes*,
and the parts that must NOT be the model's job live here as deterministic code:

* **Budget cap.** Total spend is hard-capped by ``config.PRODUCTS_BUDGET_USD`` (0 = disabled).
  :func:`create_render` refuses any render that would push cumulative ``cost_usd`` over the
  cap — so a runaway or misconfigured backend cannot spend past the ceiling.
* **No automatic publication.** Nothing here publishes to a client. A render is a draft in
  HUMAN_REVIEW state; a human approves it, confirms rights/consent, and only then may export.
* **Export gate.** :func:`export_job` refuses unless the job is ``approved`` AND
  ``consent_confirmed`` — the single, deliberate, human-gated outbound step.

The capability is **dormant**: with no ``PRODUCTS_RENDER_URL`` and a 0 budget (the defaults),
:func:`is_enabled` is False and ``create_render`` refuses everything. Nothing in the running
app calls this module yet — it is the floor a future render worker + review UI build on
(ADR 0021), so the guards exist and are tested before any image is ever generated.
"""

from __future__ import annotations

import logging

from . import ai_runs, audit, config, db
from .providers import Capability, ProviderResult, ResultStatus, ReviewRequirement

log = logging.getLogger("mise.products")

JOB_STATUSES = ("draft", "approved", "rejected")


class ProductError(ValueError):
    """A product-job operation that violates a guard. Non-mutating."""


class BudgetError(ProductError):
    """A render that would exceed the spend cap (or the capability is disabled)."""


class ExportError(ProductError):
    """An export attempted before the approval + consent gate is satisfied."""


def is_enabled() -> bool:
    """Armed only when a render backend is configured AND a positive budget is set. Both
    default off, so the capability is dormant out of the box."""
    return bool(config.PRODUCTS_RENDER_URL) and config.PRODUCTS_BUDGET_USD > 0


def spend_to_date() -> float:
    """Cumulative recorded product-render spend (USD)."""
    row = db.one("SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM product_jobs")
    return float(row["spent"] or 0.0) if row else 0.0


def budget_remaining() -> float:
    """Headroom under the cap (never negative). 0 when disabled or fully spent."""
    return max(0.0, float(config.PRODUCTS_BUDGET_USD) - spend_to_date())


def can_spend(est_usd: float) -> bool:
    """Pre-flight: would a render costing ``est_usd`` fit under the cap while enabled?"""
    return is_enabled() and est_usd >= 0 and est_usd <= budget_remaining()


def create_render(
    gallery_id: int,
    *,
    source_asset_id: int | None = None,
    kind: str = "variant",
    spec: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost_usd: float = 0.0,
    output_path: str | None = None,
) -> int:
    """Record a product render as a draft job — the budget-guarded write.

    Refuses (:class:`BudgetError`) when the capability is disabled or when ``cost_usd`` would
    push cumulative spend over ``PRODUCTS_BUDGET_USD``. The job lands at ``draft``
    (HUMAN_REVIEW); a human approves, confirms consent, then exports. Provenance is recorded
    to ai_runs best-effort so the cost report sees product spend. Nothing is published."""
    if not is_enabled():
        raise BudgetError("product rendering is disabled (set MISE_PRODUCTS_RENDER_URL + budget)")
    if cost_usd < 0:
        raise BudgetError("cost_usd cannot be negative")
    if cost_usd > budget_remaining():
        raise BudgetError(
            f"render cost ${cost_usd:.2f} exceeds remaining budget ${budget_remaining():.2f}"
        )
    with db.tx() as con:
        cur = con.execute(
            """INSERT INTO product_jobs
                   (gallery_id, source_asset_id, kind, spec, provider, model, cost_usd, output_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (gallery_id, source_asset_id, kind, spec, provider, model, cost_usd, output_path),
        )
        job_id = cur.lastrowid
        audit.log(
            con,
            "product_job",
            job_id,
            "product_render_created",
            diff={"gallery_id": gallery_id, "cost_usd": cost_usd, "kind": kind},
        )
    _record_provenance(gallery_id, job_id, provider, model, cost_usd)
    return job_id


def _record_provenance(
    gallery_id: int, job_id: int, provider: str | None, model: str | None, cost_usd: float
) -> None:
    try:
        ai_runs.record(
            ProviderResult(
                capability=Capability.PRODUCTS,
                provider=provider or "aphrodite",
                status=ResultStatus.OK,
                review=ReviewRequirement.EXPLICIT_COMMIT,
                output={"job_id": job_id},
                model=model or None,
                cost_usd=cost_usd,
            ),
            subject_type="gallery",
            subject_id=gallery_id,
            correlation_id=f"product:gallery:{gallery_id}:{job_id}",
        )
    except Exception:
        log.exception("product provenance failed for gallery %s job %s", gallery_id, job_id)


def get_job(job_id: int) -> dict | None:
    row = db.one("SELECT * FROM product_jobs WHERE id=?", (job_id,))
    return dict(row) if row else None


def list_jobs(gallery_id: int | None = None, status: str | None = None) -> list[dict]:
    sql = """SELECT j.*, g.slug, g.title
             FROM product_jobs j JOIN galleries g ON g.id = j.gallery_id WHERE 1=1"""
    params: list = []
    if gallery_id is not None:
        sql += " AND j.gallery_id=?"
        params.append(gallery_id)
    if status:
        sql += " AND j.status=?"
        params.append(status)
    sql += " ORDER BY j.created_at DESC, j.id DESC"
    return [dict(r) for r in db.all_(sql, tuple(params))]


def set_status(job_id: int, status: str) -> None:
    """Human review transition (draft -> approved/rejected). Records the decision; publishes
    and charges nothing. Raises on an unknown status."""
    if status not in JOB_STATUSES:
        raise ProductError(f"invalid product job status: {status!r}")
    with db.tx() as con:
        con.execute(
            "UPDATE product_jobs SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, job_id),
        )
        audit.log(con, "product_job", job_id, f"product_{status}")


def confirm_consent(job_id: int, confirmed: bool = True) -> None:
    """Human confirms (or withdraws) that rights/consent are cleared for this render — the
    audit §13.5 gate the export check requires."""
    with db.tx() as con:
        con.execute(
            "UPDATE product_jobs SET consent_confirmed=?, updated_at=datetime('now') WHERE id=?",
            (1 if confirmed else 0, job_id),
        )
        audit.log(
            con, "product_job", job_id, "product_consent", diff={"confirmed": bool(confirmed)}
        )


def export_job(job_id: int) -> dict:
    """The single outbound gate: mark a render exported for use. Refuses
    (:class:`ExportError`) unless the job is APPROVED and consent is CONFIRMED — there is no
    automatic publication, and no export without an explicit human approval + consent."""
    job = get_job(job_id)
    if not job:
        raise ExportError(f"no product job #{job_id}")
    if job["status"] != "approved":
        raise ExportError("approve the render before exporting it")
    if not job["consent_confirmed"]:
        raise ExportError("confirm rights/consent before exporting")
    with db.tx() as con:
        con.execute(
            "UPDATE product_jobs SET exported_at=datetime('now'), updated_at=datetime('now') "
            "WHERE id=?",
            (job_id,),
        )
        audit.log(con, "product_job", job_id, "product_exported")
    return get_job(job_id)
