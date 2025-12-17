import pytest

from paperless_ai_titles.clients.llm_client import TitleEvaluation, TitleSuggestion
from paperless_ai_titles.core.config import Settings
from paperless_ai_titles.core.database import db_session
from paperless_ai_titles.core.models import (
    DocumentRecord,
    DocumentStatus,
    ProcessingJob,
    ProcessingJobStatus,
)
from paperless_ai_titles.services import processing as processing_module
from paperless_ai_titles.services.processing import ProcessingService


class StubPaperlessClient:
    def __init__(self, document_payload: dict):
        self.document = document_payload
        self.updated_titles: list[tuple[int, str]] = []
        self.custom_field_updates: list[tuple[int, str, str]] = []

    async def fetch_document(self, document_id: int, expand: str | None = None):
        return dict(self.document)

    async def update_title(self, document_id: int, title: str):
        self.updated_titles.append((document_id, title))
        return {"id": document_id, "title": title}

    async def set_custom_field(self, document_id: int, slug: str, value: str):
        self.custom_field_updates.append((document_id, slug, value))
        return {"id": document_id, "field": slug, "value": value}


class StubLLMClient:
    def __init__(self, suggestion: TitleSuggestion, evaluation: TitleEvaluation | None = None):
        self.suggestion = suggestion
        self.evaluation = evaluation

    async def propose_title(self, text: str, metadata: dict | None = None):
        return self.suggestion

    async def evaluate_title(self, title: str, text: str):
        if self.evaluation is None:
            raise AssertionError("evaluation requested unexpectedly")
        return self.evaluation


def _create_job(document_id: int = 1) -> int:
    with db_session() as session:
        job = ProcessingJob(
            document_id=document_id,
            status=ProcessingJobStatus.QUEUED.value,
            source="test",
        )
        session.add(job)
        session.flush()
        return job.id


def _build_service(document_payload: dict, suggestion: TitleSuggestion, *, settings: Settings | None = None, evaluation: TitleEvaluation | None = None) -> ProcessingService:
    service = ProcessingService(settings=settings or Settings())
    service.paperless = StubPaperlessClient(document_payload)
    service.llm = StubLLMClient(suggestion, evaluation)
    return service


@pytest.mark.asyncio
async def test_run_job_auto_applies_title_and_updates_metadata():
    doc = {
        "id": 1,
        "title": "",
        "content": "Invoice 2023",
        "tags": [],
        "custom_fields": {},
    }
    suggestion = TitleSuggestion(title="Invoice 2023-01", raw={"title": "Invoice 2023-01"}, confidence=0.92)
    service = _build_service(doc, suggestion)
    job_id = _create_job(1)

    await service.run_job(job_id, 1)

    assert service.paperless.updated_titles == [(1, "Invoice 2023-01")]
    assert service.paperless.custom_field_updates and service.paperless.custom_field_updates[0][1]
    with db_session() as session:
        record = session.get(DocumentRecord, 1)
        assert record.status == DocumentStatus.COMPLETED.value
        assert record.ai_title == "Invoice 2023-01"
        assert record.lock_reason is None
        job = session.get(ProcessingJob, job_id)
        assert job.status == ProcessingJobStatus.COMPLETED.value
        assert job.llm_response is not None


@pytest.mark.asyncio
async def test_run_job_routes_to_manual_approval_when_confidence_low():
    doc = {"id": 2, "title": "", "content": "Body", "tags": []}
    suggestion = TitleSuggestion(title="Draft", raw={"title": "Draft"}, confidence=0.3)
    custom_settings = Settings(llm_confidence_threshold=0.8)
    service = _build_service(doc, suggestion, settings=custom_settings)
    job_id = _create_job(2)

    await service.run_job(job_id, 2)

    with db_session() as session:
        record = session.get(DocumentRecord, 2)
        assert record.status == DocumentStatus.AWAITING_APPROVAL.value
        assert "confidence" in (record.lock_reason or "")
        assert record.extra and "pending" in record.extra
        job = session.get(ProcessingJob, job_id)
        assert job.status == ProcessingJobStatus.AWAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_approve_pending_applies_stored_plan():
    doc = {"id": 3, "title": "", "content": "Text", "tags": []}
    suggestion = TitleSuggestion(title="Approved", raw={"title": "Approved"}, confidence=0.4)
    service = _build_service(doc, suggestion, settings=Settings(llm_confidence_threshold=0.9))
    job_id = _create_job(3)

    await service.run_job(job_id, 3)
    # Pending approval now exists; approving should update title
    service.paperless.updated_titles.clear()

    await service.approve_pending(3)

    assert service.paperless.updated_titles == [(3, "Approved")]
    with db_session() as session:
        record = session.get(DocumentRecord, 3)
        assert record.status == DocumentStatus.COMPLETED.value
        assert record.ai_title == "Approved"
        job = session.get(ProcessingJob, job_id)
        assert job.status == ProcessingJobStatus.COMPLETED.value


def test_run_processing_job_marks_failure(monkeypatch):
    original_service_cls = processing_module.ProcessingService

    class ExplodingProcessingService:
        def __init__(self):
            pass

        async def run_job(self, job_id: int, document_id: int):
            raise RuntimeError("Boom from worker")

        def mark_failure(self, job_id: int, document_id: int, error: str):
            return original_service_cls.mark_failure(self, job_id, document_id, error)

    monkeypatch.setattr(processing_module, "ProcessingService", ExplodingProcessingService)

    document_id = 5
    job_id = _create_job(document_id)

    with pytest.raises(RuntimeError):
        processing_module.run_processing_job(job_id, document_id)

    with db_session() as session:
        job = session.get(ProcessingJob, job_id)
        assert job.status == ProcessingJobStatus.FAILED.value
        assert job.last_error and "Boom from worker" in job.last_error
        record = session.get(DocumentRecord, document_id)
        assert record.status == DocumentStatus.FAILED.value


def _create_pending_record(document_id: int, job_id: int) -> None:
    snapshot = {
        "job_id": job_id,
        "new_title": "Rejected Title",
        "reason": "manual test",
        "existing_title": "Old",
    }
    with db_session() as session:
        record = DocumentRecord(
            document_id=document_id,
            status=DocumentStatus.AWAITING_APPROVAL.value,
            extra={"pending": snapshot},
        )
        session.add(record)


def test_deny_pending_updates_record_and_job():
    service = ProcessingService(settings=Settings())
    document_id = 4
    job_id = _create_job(document_id)
    _create_pending_record(document_id, job_id)

    service.deny_pending(document_id, reason="not good")

    with db_session() as session:
        record = session.get(DocumentRecord, document_id)
        assert record.status == DocumentStatus.REJECTED.value
        assert record.lock_reason == "not good"
        assert not record.extra or "pending" not in record.extra
        job = session.get(ProcessingJob, job_id)
        assert job.status == ProcessingJobStatus.REJECTED.value
