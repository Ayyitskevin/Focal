import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from . import config, db

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.globals["site_name"] = config.SITE_NAME
templates.env.globals["base_url"] = config.BASE_URL
# cache-buster for /static/ URLs — Cloudflare edge-caches them for hours,
# so deploys must change the URL, not wait out the TTL
templates.env.globals["static_rev"] = int(max(
    (f.stat().st_mtime for f in (ROOT / "static").glob("*") if f.is_file()),
    default=0))


def _og_image_id() -> int | None:
    row = db.one("""SELECT id FROM assets WHERE portfolio=1 AND status='ready'
                    AND kind='photo' ORDER BY id LIMIT 1""")
    return row["id"] if row else None


templates.env.globals["og_image_id"] = _og_image_id


def _diff_tokens(value):
    """Presentation-only: flatten an audit-diff value into display tokens.
    JSON-array strings (how territory/channels are stored) become their elements;
    real lists pass through; scalars become a single token. Read-side cosmetics
    only — never re-encodes or mutates the stored diff."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return [str(value)]


templates.env.filters["diff_tokens"] = _diff_tokens
