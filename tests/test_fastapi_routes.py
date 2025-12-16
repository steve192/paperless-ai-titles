import pytest
from fastapi.testclient import TestClient

from paperless_ai_titles.core.database import db_session
from paperless_ai_titles.core.models import DocumentRecord, DocumentStatus, ProcessingJob, ProcessingJobStatus


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