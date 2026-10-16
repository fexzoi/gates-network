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
