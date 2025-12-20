from __future__ import annotations

from .models import DocumentStatus, ProcessingJobStatus

PROCESSING_JOB_ACTIVE_STATUSES = frozenset(
    [
        ProcessingJobStatus.QUEUED.value,
        ProcessingJobStatus.RUNNING.value,
        ProcessingJobStatus.PENDING.value,
        ProcessingJobStatus.AWAITING_APPROVAL.value,
    ]
)

PROCESSING_JOB_COMPLETED_STATUSES = frozenset(
    [
        ProcessingJobStatus.COMPLETED.value,
        ProcessingJobStatus.SKIPPED.value,
        ProcessingJobStatus.REJECTED.value,
    ]
)

PROCESSING_JOB_ALL_STATUSES = tuple(status.value for status in ProcessingJobStatus)
PROCESSING_JOB_ALL_STATUS_SET = frozenset(PROCESSING_JOB_ALL_STATUSES)

DOCUMENT_FINALIZED_STATUSES = frozenset(
    [
        DocumentStatus.COMPLETED.value,
        DocumentStatus.SKIPPED.value,
        DocumentStatus.REJECTED.value,
    ]
)


def is_document_finalized(status: str | None) -> bool:
    if not status:
        return False
    return status.lower() in DOCUMENT_FINALIZED_STATUSES
