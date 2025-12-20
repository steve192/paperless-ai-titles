import pytest
from fastapi.testclient import TestClient

from paperless_ai_titles.core.database import db_session
from paperless_ai_titles.core.models import DocumentRecord, DocumentStatus, ProcessingJob, ProcessingJobStatus
from paperless_ai_titles.services.onboarding import OnboardingConnectionError


@pytest.fixture
def api_client(monkeypatch):
    from paperless_ai_titles import fastapi_app

    async def noop():
        return None

    monkeypatch.setattr(fastapi_app.scanner_service, "start", noop)
    monkeypatch.setattr(fastapi_app.scanner_service, "stop", noop)

    with TestClient(fastapi_app.app) as client:
        yield client


def test_health_endpoint(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_queue_metrics_counts_statuses(api_client):
    with db_session() as session:
        session.add(ProcessingJob(document_id=1, status=ProcessingJobStatus.QUEUED.value))
        session.add(ProcessingJob(document_id=2, status=ProcessingJobStatus.RUNNING.value))
        session.add(ProcessingJob(document_id=3, status=ProcessingJobStatus.FAILED.value))
    response = api_client.get("/api/queue/metrics")
    body = response.json()
    assert body["queued"] == 1
    assert body["running"] == 1
    assert body["failed"] == 1


def test_job_history_endpoint_supports_pagination(api_client):
    with db_session() as session:
        for idx in range(5):
            session.add(
                ProcessingJob(
                    document_id=idx + 1,
                    status=ProcessingJobStatus.COMPLETED.value,
                    source="scanner" if idx % 2 == 0 else "manual",
                )
            )
    response = api_client.get("/api/jobs/history", params={"limit": 2, "page": 2, "status": "completed"})
    body = response.json()
    assert body["total"] == 5
    assert body["page"] == 2
    assert len(body["items"]) == 2
    assert all(item["status"] == ProcessingJobStatus.COMPLETED.value for item in body["items"])


def test_approvals_endpoint_formats_pending_records(api_client):
    pending = {
        "new_title": "AI Suggestion",
        "reason": "low confidence",
        "confidence": 0.42,
        "existing_title": "Old title",
        "created_at": "2023-10-10T10:00:00Z",
    }
    with db_session() as session:
        session.add(
            DocumentRecord(
                document_id=10,
                status=DocumentStatus.AWAITING_APPROVAL.value,
                extra={"pending": pending},
            )
        )
    response = api_client.get("/api/approvals")
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["document_id"] == 10
    assert item["suggested_title"] == "AI Suggestion"
    assert item["reason"].startswith("low confidence")


def test_setup_preview_propagates_connection_error_details(api_client, monkeypatch):
    from paperless_ai_titles.services import onboarding as onboarding_module

    async def fake_preview(self, overrides, page_size=10):  # noqa: ARG002
        raise OnboardingConnectionError(
            "paperless",
            url="https://paperless.example.com/api/documents/",
            status_code=401,
            message="Unauthorized",
        )

    monkeypatch.setattr(onboarding_module.OnboardingService, "preview_documents", fake_preview)

    response = api_client.post("/api/setup/preview-documents", json={"settings": {}})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["service"] == "paperless"
    assert detail["status_code"] == 401
    assert "paperless.example.com" in detail["url"]
    assert "Unauthorized" in detail["message"]


def test_setup_complete_requires_successful_connections(api_client, monkeypatch):
    from paperless_ai_titles.services import onboarding as onboarding_module

    async def failing_validate(self, overrides):  # noqa: ARG002
        raise OnboardingConnectionError(
            "llm",
            url="http://llm.local/v1/chat/completions",
            status_code=500,
            message="Internal error",
        )

    async def guard_complete(self, overrides):  # pragma: no cover - should not run
        raise AssertionError("complete() should not be called when validation fails")

    monkeypatch.setattr(onboarding_module.OnboardingService, "validate_connections", failing_validate)
    monkeypatch.setattr(onboarding_module.OnboardingService, "complete", guard_complete)

    response = api_client.post("/api/setup/complete", json={"settings": {}})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["service"] == "llm"
    assert detail["status_code"] == 500
    assert "llm.local" in detail["url"]
    assert "Internal error" in detail["message"]


def test_force_reprocess_filters_applied_and_denied(api_client, monkeypatch):
    from paperless_ai_titles.routers import api as api_module

    queued: list[int] = []

    def fake_enqueue(document_id, source="manual", reason=None, force=False):  # noqa: ARG001
        queued.append(document_id)
        return None, True

    monkeypatch.setattr(api_module, "enqueue_document", fake_enqueue)

    with db_session() as session:
        session.add(
            DocumentRecord(
                document_id=1,
                status=DocumentStatus.COMPLETED.value,
                ai_title="Applied title",
            )
        )
        session.add(DocumentRecord(document_id=2, status=DocumentStatus.REJECTED.value))
        session.add(DocumentRecord(document_id=3, status=DocumentStatus.FAILED.value))
        session.add(DocumentRecord(document_id=4, status=DocumentStatus.COMPLETED.value))

    response = api_client.post(
        "/api/force-reprocess",
        json={
            "scope": "all",
            "ignore_documents_with_applied_title_changes": True,
            "ignore_documents_with_denied_title_changes": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 2
    assert set(queued) == {3, 4}


def test_force_reprocess_explicit_ids_can_error_when_all_excluded(api_client, monkeypatch):
    from paperless_ai_titles.routers import api as api_module

    def fake_enqueue(document_id, source="manual", reason=None, force=False):  # noqa: ARG001
        raise AssertionError("enqueue_document should not be called")

    monkeypatch.setattr(api_module, "enqueue_document", fake_enqueue)

    with db_session() as session:
        session.add(
            DocumentRecord(
                document_id=10,
                status=DocumentStatus.COMPLETED.value,
                ai_title="Applied title",
            )
        )
        session.add(DocumentRecord(document_id=11, status=DocumentStatus.REJECTED.value))

    response = api_client.post(
        "/api/force-reprocess",
        json={
            "scope": "selected",
            "document_ids": [10, 11],
            "ignore_documents_with_applied_title_changes": True,
            "ignore_documents_with_denied_title_changes": True,
        },
    )
    assert response.status_code == 400
    assert "No documents matched reprocess criteria" in response.json()["detail"]
