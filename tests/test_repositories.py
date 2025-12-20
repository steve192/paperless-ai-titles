from datetime import datetime, timedelta

from paperless_ai_titles.core.models import DocumentRecord, DocumentStatus, ProcessingJob, ProcessingJobStatus
from paperless_ai_titles.core.status_sets import is_document_finalized
from paperless_ai_titles.repositories.unit_of_work import UnitOfWork


def test_document_record_filter_ids_excludes_applied_and_denied():
    with UnitOfWork() as uow:
        uow.documents.add(
            DocumentRecord(
                document_id=1,
                status=DocumentStatus.COMPLETED.value,
                ai_title="Applied title",
            )
        )
        uow.documents.add(DocumentRecord(document_id=2, status=DocumentStatus.REJECTED.value))
        uow.documents.add(DocumentRecord(document_id=3, status=DocumentStatus.AWAITING_APPROVAL.value))

    with UnitOfWork() as uow:
        filtered = uow.documents.filter_ids(
            [1, 2, 3, 4],
            exclude_applied=True,
            exclude_denied=True,
        )
        assert filtered == [3, 4]


def test_document_record_find_ids_respects_status_and_exclusions():
    with UnitOfWork() as uow:
        uow.documents.add(
            DocumentRecord(
                document_id=10,
                status=DocumentStatus.FAILED.value,
                ai_title="Applied title",
            )
        )
        uow.documents.add(DocumentRecord(document_id=11, status=DocumentStatus.FAILED.value))
        uow.documents.add(DocumentRecord(document_id=12, status=DocumentStatus.REJECTED.value))

    with UnitOfWork() as uow:
        ids = uow.documents.find_ids(
            status=DocumentStatus.FAILED.value,
            exclude_applied=True,
            exclude_denied=True,
        )
        assert ids == [11]


def test_processing_job_count_completed_since_uses_completed_statuses():
    now = datetime.utcnow()
    with UnitOfWork() as uow:
        uow.jobs.add(
            ProcessingJob(
                document_id=1,
                status=ProcessingJobStatus.COMPLETED.value,
                completed_at=now,
            )
        )
        uow.jobs.add(
            ProcessingJob(
                document_id=2,
                status=ProcessingJobStatus.SKIPPED.value,
                completed_at=now,
            )
        )
        uow.jobs.add(
            ProcessingJob(
                document_id=3,
                status=ProcessingJobStatus.REJECTED.value,
                completed_at=now,
            )
        )
        uow.jobs.add(
            ProcessingJob(
                document_id=4,
                status=ProcessingJobStatus.FAILED.value,
                completed_at=now,
            )
        )
        uow.jobs.add(
            ProcessingJob(
                document_id=5,
                status=ProcessingJobStatus.COMPLETED.value,
                completed_at=now - timedelta(days=1),
            )
        )

    with UnitOfWork() as uow:
        count = uow.jobs.count_completed_since(now - timedelta(hours=1))
        assert count == 3


def test_is_document_finalized_matches_status_set():
    assert is_document_finalized(DocumentStatus.COMPLETED.value) is True
    assert is_document_finalized(DocumentStatus.REJECTED.value) is True
    assert is_document_finalized(DocumentStatus.PENDING.value) is False
    assert is_document_finalized(None) is False
