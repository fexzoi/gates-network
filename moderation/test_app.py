import os

os.environ.setdefault("MODERATION_API_TOKEN", "test-secret")

import pytest
from fastapi.testclient import TestClient
import app as moderation_app

client = TestClient(moderation_app.app)
HEADERS = {"Authorization": "Bearer test-secret"}
PAYLOAD = {
    "submission_id": "test-submission-001",
    "title": "Example digital design service",
    "platform": "Other",
    "category": "Digital services",
    "description": "A legitimate digital design service with clear deliverables.",
    "price_text": "$10",
    "seller_name": "Example Seller",
    "seller_contact_url": "https://example.com/contact",
}


@pytest.fixture(autouse=True)
def isolated_audit_database(tmp_path, monkeypatch):
    # Each test gets a real temporary SQLite file; separate :memory: connections do not share tables.
    monkeypatch.setattr(moderation_app, "DB_PATH", str(tmp_path / "audit.sqlite3"))



def test_support_request_is_saved_and_returns_reference(monkeypatch):
    response = client.post("/v1/support", json={"message": "How do I check my listing review?", "session_id": "test-user"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket_id"]
    assert "GTS Support" not in payload["answer"] or len(payload["answer"]) > 20
    with moderation_app._db() as connection:
        row = connection.execute("SELECT message, session_id, status FROM support_requests WHERE ticket_id = ?", (payload["ticket_id"],)).fetchone()
    assert row["message"] == "How do I check my listing review?"
    assert row["session_id"] == "test-user"
    assert row["status"] in {"fallback", "ai_answered"}

def test_support_rejects_too_short_message():
    response = client.post("/v1/support", json={"message": "x"})
    assert response.status_code == 422

def test_health_discloses_publishing_is_disabled():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["publishing_enabled"] is False


def test_moderation_requires_authentication():
    response = client.post("/v1/moderate", json=PAYLOAD)
    assert response.status_code == 401


def test_successful_decision_is_logged_and_never_published(monkeypatch):
    monkeypatch.setattr(moderation_app, "moderate_with_openai", lambda listing: moderation_app.Decision(
        decision="approve", risk_score=5, confidence=96, reasons=["Test listing passed policy checks."], policy_flags=[]
    ))
    response = client.post("/v1/moderate", json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["decision"] == "approve"
    assert result["published"] is False
    assert result["publication_status"] == "disabled_pending_integration_and_tests"
    audit = client.get("/v1/audit", headers=HEADERS)
    assert audit.status_code == 200
    assert audit.json()["items"][0]["submission_id"] == PAYLOAD["submission_id"]


def test_low_confidence_approval_is_rejected_by_safety_gate(monkeypatch):
    monkeypatch.setattr(moderation_app, "moderate_with_openai", lambda listing: moderation_app.Decision(
        decision="approve", risk_score=10, confidence=50, reasons=["Uncertain listing."], policy_flags=[]
    ))
    response = client.post("/v1/moderate", json={**PAYLOAD, "submission_id": "test-low-confidence"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["decision"] == "reject"


def test_openai_failure_fails_closed(monkeypatch):
    def fail(_listing):
        raise RuntimeError("simulated provider outage")
    monkeypatch.setattr(moderation_app, "moderate_with_openai", fail)
    response = client.post("/v1/moderate", json={**PAYLOAD, "submission_id": "test-provider-failure"}, headers=HEADERS)
    assert response.status_code == 503
    assert "not published" in response.json()["detail"]
    audit = client.get("/v1/audit", headers=HEADERS, params={"submission_id": "test-provider-failure"})
    assert audit.status_code == 200
    assert audit.json()["items"][0]["status"] == "error"


def test_wix_adapter_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(moderation_app, "WIX_AUTOMATION_ENABLED", False)
    response = client.post("/v1/wix/process-pending", headers=HEADERS)
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()



def test_wix_status_patch_is_conditional_and_partial(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"dataItem": {
                "id": "submission-123",
                "data": {"status": "AI Rejected", "_id": "submission-123"}
            }}

    def fake_patch(url, *, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(moderation_app.httpx, "patch", fake_patch)
    monkeypatch.setattr(moderation_app, "WIX_API_KEY", "test-wix-key")
    monkeypatch.setattr(moderation_app, "WIX_SITE_ID", "test-site-id")
    moderation_app._wix_mark_moderated("submission-123", "AI Rejected")

    assert captured["url"].endswith("/wix-data/v2/items/submission-123")
    assert captured["json"]["patch"]["fieldModifications"][0]["fieldPath"] == "status"
    assert captured["json"]["patch"]["fieldModifications"][0]["setFieldOptions"]["value"] == "AI Rejected"

    assert captured["json"]["condition"]["filter"] == {"status": "Pending review"}


def test_wix_status_patch_fails_if_response_does_not_confirm_transition(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"dataItem": {"id": "submission-123", "data": {"status": "Pending review"}}}

    monkeypatch.setattr(moderation_app.httpx, "patch", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(moderation_app, "WIX_API_KEY", "test-wix-key")
    monkeypatch.setattr(moderation_app, "WIX_SITE_ID", "test-site-id")
    with pytest.raises(RuntimeError, match="wix_conditional_status_transition_not_confirmed"):
        moderation_app._wix_patch_status("submission-123", "Rejected", "Pending review")


def test_contact_url_validation_rejects_non_http_and_malformed_urls():
    assert moderation_app._valid_contact_url("https://example.com/contact")
    assert moderation_app._valid_contact_url("http://example.com")
    assert not moderation_app._valid_contact_url("javascript:alert(1)")
    assert not moderation_app._valid_contact_url("ftp://example.com/file")
    assert not moderation_app._valid_contact_url("https:///missing-host")
    assert not moderation_app._valid_contact_url("https://example.com/a b")
    assert not moderation_app._valid_contact_url("not-a-url")


def test_dashboard_is_served_without_caching():
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Moderation Console" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "function safeHttpUrl(value)" in response.text
    assert "contact.href = safeContactUrl" in response.text
    assert "contact.href = item.sellerContactUrl" not in response.text


def test_admin_queue_requires_authentication():
    response = client.get("/v1/wix/submissions")
    assert response.status_code == 401


def test_admin_decision_requires_authentication():
    response = client.post("/v1/wix/submissions/submission-123/decision", json={"action":"reject","reason":"Policy violation"})
    assert response.status_code == 401


def test_admin_queue_stays_disabled_until_wix_integration_enabled(monkeypatch):
    monkeypatch.setattr(moderation_app, "WIX_AUTOMATION_ENABLED", False)
    response = client.get("/v1/wix/submissions", headers=HEADERS)
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()


def test_admin_decision_reason_validation():
    response = client.post("/v1/wix/submissions/submission-123/decision",
                           json={"action":"reject","reason":"x"}, headers=HEADERS)
    assert response.status_code == 422


def test_admin_approval_publishes_only_after_explicit_human_decision(monkeypatch):
    monkeypatch.setattr(moderation_app, "WIX_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(moderation_app, "WIX_API_KEY", "test-wix-key")
    monkeypatch.setattr(moderation_app, "WIX_SITE_ID", "test-site-id")
    monkeypatch.setattr(moderation_app, "_wix_query_status", lambda status, limit=100: [
        {"id": "submission-123", "data": {
            "title": "Digital design service", "productName": "Design pack",
            "platform": "Other", "category": "services", "description": "Design work",
            "priceText": "₹500", "sellerName": "Seller", "sellerContactUrl": "https://example.com"
        }}
    ] if status == "Pending review" else [])
    transitions = []
    monkeypatch.setattr(moderation_app, "_wix_patch_status",
                        lambda item_id, status, expected_status: transitions.append((item_id, status, expected_status)))
    monkeypatch.setattr(moderation_app, "_wix_insert_public_listing", lambda submission_id, data: "public-listing-123")
    result = moderation_app.admin_decide_submission(
        "submission-123",
        moderation_app.AdminDecisionRequest(action="approve", reason="Reviewed details and seller contact.")
    )
    assert result["published"] is True
    assert result["status"] == "Approved"
    assert transitions == [
        ("submission-123", "Publishing", "Pending review"),
        ("submission-123", "Approved", "Publishing"),
    ]
    audit = client.get("/v1/audit", headers=HEADERS, params={"submission_id": "submission-123"})
    assert audit.status_code == 200
    assert audit.json()["items"][0]["status"] == "manual_approved_published"


def test_admin_rejection_never_publishes(monkeypatch):
    monkeypatch.setattr(moderation_app, "WIX_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(moderation_app, "WIX_API_KEY", "test-wix-key")
    monkeypatch.setattr(moderation_app, "WIX_SITE_ID", "test-site-id")
    monkeypatch.setattr(moderation_app, "_wix_query_status", lambda status, limit=100: [
        {"id": "submission-reject-123", "data": {"title": "Unsafe listing"}}
    ] if status == "Pending review" else [])
    transitions = []
    monkeypatch.setattr(moderation_app, "_wix_patch_status",
                        lambda item_id, status, expected_status: transitions.append((item_id, status, expected_status)))
    monkeypatch.setattr(moderation_app, "_wix_insert_public_listing",
                        lambda *args: (_ for _ in ()).throw(AssertionError("Rejected listings must never publish")))
    result = moderation_app.admin_decide_submission(
        "submission-reject-123",
        moderation_app.AdminDecisionRequest(action="reject", reason="The listing violates marketplace rules.")
    )
    assert result["published"] is False
    assert transitions == [("submission-reject-123", "Rejected", "Pending review")]


def test_wix_query_status_reads_all_pages(monkeypatch):
    monkeypatch.setattr(moderation_app, "WIX_API_KEY", "test-wix-key")
    monkeypatch.setattr(moderation_app, "WIX_SITE_ID", "test-site-id")
    requests = []

    class FakeResponse:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            return None

        def json(self):
            return {"dataItems": self._items}

    pages = {
        0: [{"id": "submission-1"}, {"id": "submission-2"}],
        2: [{"id": "submission-3"}],
    }

    def fake_post(url, headers, json=None, timeout=None):
        requests.append(json["query"]["paging"]["offset"])
        return FakeResponse(pages.get(json["query"]["paging"]["offset"], []))

    monkeypatch.setattr(moderation_app.httpx, "post", fake_post)
    items = moderation_app._wix_query_status("Pending review", limit=2)

    assert [item["id"] for item in items] == ["submission-1", "submission-2", "submission-3"]
    assert requests == [0, 2]
