"""Capability isolation and safe DTO tests for native client delivery reads."""

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import config, db, mobile_client_delivery_api, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


def _device() -> dict:
    return {
        "installation_id": "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98",
        "name": "Client iPhone",
        "platform": "ios",
        "app_version": "1.0",
    }


def _unlock(
    client: TestClient,
    kind: str,
    slug: str,
    pin: str | None = None,
) -> tuple[dict[str, str], dict]:
    if kind in {"proposal", "contract", "invoice"}:
        path = "/api/v1/client-auth/document/exchange"
    else:
        path = f"/api/v1/client-auth/{kind}/unlock"
    body = {"kind": kind, "slug": slug, "device": _device()}
    if pin is not None:
        body["pin"] = pin
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}, payload


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "client-delivery-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    ratelimit._hits.clear()
    db.migrate()

    client_id = db.run(
        """INSERT INTO clients (name, company, email, notes, usage_rights)
           VALUES (?,?,?,?,?)""",
        (
            "Avery Client",
            "Avery Foods",
            "avery@example.test",
            "PRIVATE-CLIENT-NOTES",
            "Client-facing legacy rights note",
        ),
    )
    portal_id = db.run(
        "INSERT INTO portals (client_id, slug, pin, published) VALUES (?,?,?,1)",
        (client_id, "avery-portal", "1234"),
    )
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,client_name,pin,published,client_id,expires_at)
           VALUES (?,?,?,?,1,?,?)""",
        (
            "summer-gallery",
            "Summer Campaign",
            "Avery",
            "9876",
            client_id,
            "2026-12-31",
        ),
    )
    db.run(
        """INSERT INTO galleries
           (slug,title,client_name,pin,published,client_id)
           VALUES ('private-gallery','PRIVATE-DRAFT-GALLERY','Avery','1111',0,?)""",
        (client_id,),
    )
    db.run(
        """INSERT INTO brand_assets (client_id,filename,stored,bytes)
           VALUES (?,?,?,?)""",
        (client_id, "avery-logo.svg", "/srv/private/avery-logo.svg", 2048),
    )
    db.run(
        """INSERT INTO licenses
           (holder_client_id,title,scope,usage_tier,exclusivity,territory,
            channels,perpetual,status,fee_cents,notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            client_id,
            "Summer usage",
            "Campaign selects",
            "extended",
            "non_exclusive",
            '["north_america"]',
            '["paid_social","website"]',
            1,
            "active",
            99_900,
            "PRIVATE-LICENSE-NOTES",
        ),
    )
    db.run(
        """INSERT INTO licenses
           (holder_client_id,title,status,fee_cents)
           VALUES (?,'PRIVATE-DRAFT-LICENSE','draft',12345)""",
        (client_id,),
    )
    project_id = db.run(
        """INSERT INTO projects
           (client_id,title,status,gallery_id,notes,notion_page_id,
            workspace_slug,workspace_pin,workspace_published)
           VALUES (?,?,?,?,?,?,?,?,1)""",
        (
            client_id,
            "Summer launch",
            "session_planning",
            gallery_id,
            "PRIVATE-PROJECT-NOTES",
            "PRIVATE-NOTION-ID",
            "summer-workspace",
            "4321",
        ),
    )
    proposal_items = json.dumps(
        [
            {
                "label": "  Creative\u0000 Direction  ",
                "qty": 2,
                "unit_cents": 2500,
                "sku": "  CREATIVE  ",
                "private": "DO-NOT-SERIALIZE",
            },
            {"label": "invalid", "qty": 0, "unit_cents": 9000},
            "not-an-item",
        ]
    )
    proposal_id = db.run(
        """INSERT INTO proposals
           (project_id,slug,title,intro,line_items,total_cents,status,
            sent_at,created_at)
           VALUES (?,?,?,?,?,?,'sent',?,?)""",
        (
            project_id,
            "summer-proposal",
            "Summer proposal",
            "A clear proposal",
            proposal_items,
            5000,
            "2026-07-01 12:00:00",
            "2026-07-01 11:00:00",
        ),
    )
    db.run(
        """INSERT INTO proposals
           (project_id,slug,title,line_items,total_cents,status)
           VALUES (?,'draft-proposal','PRIVATE-DRAFT-PROPOSAL','[]',1,'draft')""",
        (project_id,),
    )
    contract_body = "# Services\n\nAvery agrees to the campaign terms."
    contract_id = db.run(
        """INSERT INTO contracts
           (project_id,slug,title,body,body_sha256,status,signer_ip,sent_at,created_at)
           VALUES (?,?,?,?,?,'sent',?,?,?)""",
        (
            project_id,
            "summer-contract",
            "Summer agreement",
            contract_body,
            hashlib.sha256(contract_body.encode()).hexdigest(),
            "203.0.113.77",
            "2026-07-02 12:00:00",
            "2026-07-02 11:00:00",
        ),
    )
    invoice_id = db.run(
        """INSERT INTO invoices
           (project_id,slug,title,line_items,total_cents,deposit_cents,due_date,
            status,stripe_session_id,terms,po_number,net_days,sent_at,created_at)
           VALUES (?,?,?,?,?,?,?,'sent',?,?,?,?,?,?)""",
        (
            project_id,
            "summer-invoice",
            "Summer invoice",
            json.dumps([{"label": "Campaign", "qty": 1, "unit_cents": 10_000}]),
            10_000,
            3_000,
            "2026-08-15",
            "cs_PRIVATE_SESSION",
            "Net 30",
            "PO-CLIENT-VISIBLE",
            30,
            "2026-07-03 12:00:00",
            "2026-07-03 11:00:00",
        ),
    )
    payment_id = db.run(
        """INSERT INTO payments
           (invoice_id,stripe_event_id,stripe_session_id,amount_cents,kind,created_at)
           VALUES (?,?,?,?,?,?)""",
        (
            invoice_id,
            "evt_PRIVATE_EVENT",
            "cs_PRIVATE_PAYMENT_SESSION",
            2500,
            "deposit",
            "2026-07-04 12:00:00",
        ),
    )

    # Another client proves resource joins cannot bleed across capabilities.
    other_client_id = db.run(
        "INSERT INTO clients (name,notes) VALUES ('Other Client','OTHER-PRIVATE-NOTES')"
    )
    other_portal_id = db.run(
        "INSERT INTO portals (client_id,slug,pin,published) VALUES (?,'other-portal','5555',1)",
        (other_client_id,),
    )
    other_project_id = db.run(
        """INSERT INTO projects
           (client_id,title,status,notes,workspace_slug,workspace_pin,workspace_published)
           VALUES (?,'OTHER-PRIVATE-PROJECT','session_planning','OTHER-NOTES',
                   'other-workspace','6666',1)""",
        (other_client_id,),
    )
    other_proposal_id = db.run(
        """INSERT INTO proposals
           (project_id,slug,title,line_items,total_cents,status)
           VALUES (?,'other-proposal','OTHER-PRIVATE-DOCUMENT','[]',999,'sent')""",
        (other_project_id,),
    )

    test_client = TestClient(app, base_url="https://studio.test")
    yield (
        test_client,
        {
            "client_id": client_id,
            "portal_id": portal_id,
            "gallery_id": gallery_id,
            "project_id": project_id,
            "proposal_id": proposal_id,
            "contract_id": contract_id,
            "invoice_id": invoice_id,
            "payment_id": payment_id,
            "contract_body": contract_body,
            "other_client_id": other_client_id,
            "other_portal_id": other_portal_id,
            "other_proposal_id": other_proposal_id,
        },
    )
    test_client.close()
    ratelimit._hits.clear()


def test_portal_is_non_transitive_safe_bounded_and_revocable(delivery):
    client, ids = delivery
    headers, session = _unlock(client, "portal", "avery-portal", "1234")

    response = client.get("/api/v1/client/portal", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["vary"] == "Authorization"
    body = response.json()
    assert set(body) == {
        "id",
        "client_display_name",
        "galleries",
        "brand_assets",
        "licenses",
        "usage_rights_note",
    }
    assert body["id"] == ids["portal_id"]
    assert body["client_display_name"] == "Avery Foods"
    assert body["galleries"] == [
        {
            "id": ids["gallery_id"],
            "title": "Summer Campaign",
            "slug": "summer-gallery",
            "expires_on": "2026-12-31",
            "created_at": body["galleries"][0]["created_at"],
        }
    ]
    assert body["galleries"][0]["created_at"].endswith("Z")
    assert "action_url" not in body["galleries"][0]
    assert body["brand_assets"][0]["byte_count"] == 2048
    assert body["licenses"] == [
        {
            "title": "Summer usage",
            "scope": "Campaign selects",
            "tier": "Extended",
            "exclusive": False,
            "territory": ["North America"],
            "channels": ["Paid Social", "Website"],
            "term": "Perpetual",
        }
    ]
    assert body["usage_rights_note"] == "Client-facing legacy rights note"
    forbidden = (
        "PRIVATE-DRAFT",
        "PRIVATE-CLIENT-NOTES",
        "PRIVATE-LICENSE-NOTES",
        "/srv/private",
        "99_900",
        '"pin"',
        "action_url",
        "OTHER-PRIVATE",
    )
    assert all(value not in response.text for value in forbidden)
    assert (
        db.one("SELECT visits,last_visit FROM portals WHERE id=?", (ids["portal_id"],))[
            "last_visit"
        ]
        is None
    )

    cached = client.get(
        "/api/v1/client/portal",
        headers={**headers, "If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["cache-control"] == "private, no-cache"

    # Scope text must name this exact portal resource, even for a valid session.
    db.run(
        "UPDATE api_sessions SET scopes_json=? WHERE id=?",
        ('["portal:999999:read"]', session["session_id"]),
    )
    assert client.get("/api/v1/client/portal", headers=headers).status_code == 403

    revoked_headers, _ = _unlock(client, "portal", "avery-portal", "1234")
    db.run("UPDATE portals SET published=0 WHERE id=?", (ids["portal_id"],))
    revoked = client.get("/api/v1/client/portal", headers=revoked_headers)
    assert revoked.status_code == 401
    assert revoked.json()["code"] == "auth.invalid_token"


def test_portal_license_hierarchy_cycle_is_guarded_and_returns_promptly(delivery):
    client, ids = delivery
    db.run(
        "UPDATE clients SET parent_id=? WHERE id=?",
        (ids["other_client_id"], ids["client_id"]),
    )
    db.run(
        "UPDATE clients SET parent_id=? WHERE id=?",
        (ids["client_id"], ids["other_client_id"]),
    )
    db.run(
        """INSERT INTO licenses
           (holder_client_id,title,coverage_scope,status)
           VALUES (?,'Ancestor cycle license','holder_and_descendants','active')""",
        (ids["other_client_id"],),
    )
    headers, _ = _unlock(client, "portal", "avery-portal", "1234")

    started = time.monotonic()
    response = client.get("/api/v1/client/portal", headers=headers)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 2
    assert [item["title"] for item in response.json()["licenses"]] == [
        "Ancestor cycle license",
        "Summer usage",
    ]


def test_workspace_only_exposes_live_children_and_safe_same_origin_links(delivery, monkeypatch):
    client, ids = delivery
    headers, _ = _unlock(client, "workspace", "summer-workspace", "4321")

    response = client.get("/api/v1/client/workspace", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache"
    body = response.json()
    assert body["id"] == ids["project_id"]
    assert body["title"] == "Summer launch"
    assert [item["kind"] for item in body["resources"]] == [
        "proposal",
        "contract",
        "invoice",
        "gallery",
    ]
    assert [item["action_url"] for item in body["resources"]] == [
        "https://studio.test/p/summer-proposal",
        "https://studio.test/c/summer-contract",
        "https://studio.test/i/summer-invoice",
        "https://studio.test/g/summer-gallery",
    ]
    assert body["resources"][0]["total"]["minor_units"] == 5000
    assert body["resources"][2]["due_on"] == "2026-08-15"
    assert body["resources"][3]["status"] == "published"
    assert "PRIVATE-DRAFT" not in response.text
    assert "PRIVATE-PROJECT-NOTES" not in response.text
    assert "PRIVATE-NOTION-ID" not in response.text
    assert "OTHER-PRIVATE" not in response.text
    assert "workspace_pin" not in response.text

    portal_headers, _ = _unlock(client, "portal", "avery-portal", "1234")
    wrong_kind = client.get("/api/v1/client/workspace", headers=portal_headers)
    assert wrong_kind.status_code == 403

    # A token is request-host bound, so a forged Host cannot manufacture links.
    forged = client.get(
        "/api/v1/client/workspace",
        headers={**headers, "Host": "attacker.test"},
    )
    assert forged.status_code == 401
    assert "attacker.test" not in forged.text

    # Even an otherwise valid token fails closed if canonical configuration and
    # request origin diverge; arbitrary configured hosts are never reflected.
    monkeypatch.setattr(config, "BASE_URL", "https://attacker.test")
    unsafe_origin = client.get("/api/v1/client/workspace", headers=headers)
    assert unsafe_origin.status_code == 400
    assert unsafe_origin.json()["code"] == "request.invalid_origin"
    assert "attacker.test" not in unsafe_origin.text
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")

    db.run("UPDATE projects SET workspace_published=0 WHERE id=?", (ids["project_id"],))
    revoked = client.get("/api/v1/client/workspace", headers=headers)
    assert revoked.status_code == 401
    assert revoked.json()["code"] == "auth.invalid_token"


def test_documents_are_read_only_variant_exact_safe_and_authoritative(delivery):
    client, ids = delivery

    proposal_headers, _ = _unlock(client, "proposal", "summer-proposal")
    proposal_response = client.get("/api/v1/client/document", headers=proposal_headers)
    assert proposal_response.status_code == 200
    proposal = proposal_response.json()
    assert proposal["kind"] == "proposal"
    assert proposal["id"] == ids["proposal_id"]
    assert proposal["status"] == "sent"
    assert proposal["detail"] == "A clear proposal"
    assert proposal["line_items"] == [
        {
            "label": "Creative  Direction",
            "quantity": 2,
            "unit_price": {"minor_units": 2500, "currency_code": "USD"},
            "sku": "CREATIVE",
        }
    ]
    assert proposal["total"] == {"minor_units": 5000, "currency_code": "USD"}
    assert proposal["payment_count"] == 0
    assert proposal["payments_truncated"] is False
    assert proposal["can_act"] is True
    assert proposal["action_url"] == "https://studio.test/p/summer-proposal"
    assert proposal["sent_at"] == "2026-07-01T12:00:00Z"
    assert (
        db.one("SELECT status,viewed_at FROM proposals WHERE id=?", (ids["proposal_id"],))["status"]
        == "sent"
    )
    assert (
        db.one("SELECT viewed_at FROM proposals WHERE id=?", (ids["proposal_id"],))["viewed_at"]
        is None
    )
    assert "DO-NOT-SERIALIZE" not in proposal_response.text

    contract_headers, _ = _unlock(client, "contract", "summer-contract")
    contract_response = client.get("/api/v1/client/document", headers=contract_headers)
    contract = contract_response.json()
    digest = hashlib.sha256(ids["contract_body"].encode()).hexdigest()
    assert contract["kind"] == "contract"
    assert contract["detail"] == ids["contract_body"]
    assert contract["document_etag"] == f"sha256:{digest}"
    assert contract["payment_count"] == 0
    assert contract["payments_truncated"] is False
    assert contract["can_act"] is True
    assert contract["action_url"] == "https://studio.test/c/summer-contract"
    assert "203.0.113.77" not in contract_response.text
    assert "signer_ip" not in contract_response.text
    assert "body_sha256" not in contract_response.text
    db.run(
        "UPDATE contracts SET body=body || ' changed' WHERE id=?",
        (ids["contract_id"],),
    )
    changed = client.get("/api/v1/client/document", headers=contract_headers)
    assert changed.status_code == 409
    assert changed.json()["code"] == "document.integrity_failed"
    assert ids["contract_body"] not in changed.text
    assert "changed" not in changed.text
    assert digest not in changed.text

    invoice_headers, _ = _unlock(client, "invoice", "summer-invoice")
    invoice_response = client.get("/api/v1/client/document", headers=invoice_headers)
    invoice = invoice_response.json()
    assert invoice["kind"] == "invoice"
    assert invoice["total"]["minor_units"] == 10_000
    assert invoice["deposit"]["minor_units"] == 3_000
    assert invoice["paid"]["minor_units"] == 2_500
    assert invoice["balance"]["minor_units"] == 7_500
    assert invoice["payment_count"] == 1
    assert invoice["payments_truncated"] is False
    assert invoice["due_on"] == "2026-08-15"
    assert invoice["payments"] == [
        {
            "id": ids["payment_id"],
            "invoice_id": ids["invoice_id"],
            "amount": {"minor_units": 2500, "currency_code": "USD"},
            "kind": "deposit",
            "created_at": "2026-07-04T12:00:00Z",
        }
    ]
    assert invoice["action_url"] == "https://studio.test/i/summer-invoice"
    assert invoice["can_act"] is True
    forbidden = (
        "cs_PRIVATE",
        "evt_PRIVATE",
        "stripe_session",
        "client_email",
        "billing_address",
        "tax_id",
        "signer_ip",
        "PRIVATE-PROJECT",
    )
    assert all(value not in invoice_response.text for value in forbidden)
    cached = client.get(
        "/api/v1/client/document",
        headers={**invoice_headers, "If-None-Match": invoice_response.headers["etag"]},
    )
    assert cached.status_code == 304

    db.run("UPDATE invoices SET status='draft' WHERE id=?", (ids["invoice_id"],))
    revoked = client.get("/api/v1/client/document", headers=invoice_headers)
    assert revoked.status_code == 401
    assert revoked.json()["code"] == "auth.invalid_token"


def test_exact_document_variant_and_resource_scope(delivery):
    client, ids = delivery
    headers, session = _unlock(client, "proposal", "summer-proposal")
    db.run(
        "UPDATE api_sessions SET scopes_json=? WHERE id=?",
        (
            f'["document:proposal:{ids["other_proposal_id"]}:read"]',
            session["session_id"],
        ),
    )
    response = client.get("/api/v1/client/document", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "auth.insufficient_scope"

    # A different legitimate document capability resolves only its own row.
    other_headers, _ = _unlock(client, "proposal", "other-proposal")
    other = client.get("/api/v1/client/document", headers=other_headers)
    assert other.status_code == 200
    assert other.json()["id"] == ids["other_proposal_id"]
    assert other.json()["title"] == "OTHER-PRIVATE-DOCUMENT"
    assert "Summer proposal" not in other.text


def test_document_action_requires_its_exact_capability_scope(delivery):
    client, ids = delivery
    headers, session = _unlock(client, "proposal", "summer-proposal")
    db.run(
        "UPDATE api_sessions SET scopes_json=? WHERE id=?",
        (
            f'["document:proposal:{ids["proposal_id"]}:read",'
            f'"document:proposal:{ids["proposal_id"]}:sign"]',
            session["session_id"],
        ),
    )

    response = client.get("/api/v1/client/document", headers=headers)

    assert response.status_code == 200
    assert response.json()["can_act"] is False
    assert response.json()["action_url"] == "https://studio.test/p/summer-proposal"


def test_invoice_payload_uses_one_read_snapshot(delivery, monkeypatch):
    _, ids = delivery
    real_connect = db.connect
    connections = []
    statements: dict[int, list[str]] = {}

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        statements[id(connection)] = []
        connection.set_trace_callback(statements[id(connection)].append)
        connections.append(connection)
        return connection

    monkeypatch.setattr(mobile_client_delivery_api.db, "connect", tracking_connect)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/client/document",
            "query_string": b"",
            "headers": [(b"host", b"studio.test")],
            "server": ("studio.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )

    invoice = mobile_client_delivery_api._invoice_payload(
        request,
        ids["invoice_id"],
        action_authorized=True,
    )

    assert invoice.payment_count == 1
    assert len(connections) == 1
    trace = "\n".join(statements[id(connections[0])])
    assert "BEGIN" in trace
    assert "FROM invoices" in trace
    assert trace.count("FROM payments") == 2


def test_legacy_payload_and_response_sizes_are_contained(delivery):
    client, ids = delivery
    valid = {"label": "Bounded", "qty": 1, "unit_cents": 1}
    assert len(mobile_client_delivery_api._line_items(json.dumps([valid] * 300))) == 250
    assert mobile_client_delivery_api._line_items("{broken") == []
    assert (
        mobile_client_delivery_api._line_items(
            " " * (mobile_client_delivery_api._MAX_LINE_ITEM_JSON_CHARS + 1)
        )
        == []
    )

    # The payment list is capped, while the aggregate paid amount still sums
    # every authoritative payment row.
    with db.tx() as connection:
        connection.executemany(
            """INSERT INTO payments
               (invoice_id,stripe_event_id,amount_cents,kind,created_at)
               VALUES (?,?,1,'balance','2026-07-05 12:00:00')""",
            [(ids["invoice_id"], f"evt_cap_{index}") for index in range(500)],
        )
    invoice_headers, _ = _unlock(client, "invoice", "summer-invoice")
    invoice = client.get("/api/v1/client/document", headers=invoice_headers).json()
    assert len(invoice["payments"]) == 500
    assert invoice["payment_count"] == 501
    assert invoice["payments_truncated"] is True
    assert invoice["payments"][0]["id"] != ids["payment_id"]
    assert invoice["paid"]["minor_units"] == 3000
    assert invoice["balance"]["minor_units"] == 7000

    contract_headers, _ = _unlock(client, "contract", "summer-contract")
    db.run(
        "UPDATE contracts SET body=? WHERE id=?",
        ("x" * (mobile_client_delivery_api._MAX_DOCUMENT_DETAIL + 1), ids["contract_id"]),
    )
    oversized = client.get("/api/v1/client/document", headers=contract_headers)
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "document.too_large"
