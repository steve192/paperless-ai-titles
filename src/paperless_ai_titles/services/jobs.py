from __future__ import annotations

import logging
from datetime import datetime
from typing import Tuple

from rq import Retry

from ..core.database import db_session
from ..core.models import (
    DocumentRecord,
    DocumentStatus,
    ProcessingJob,
    ProcessingJobStatus,
)
from ..queue_factory import get_queue
from ..rq_task_handlers import process_document
from ..services.settings import SettingsService

ACTIVE_STATUSES = {
    ProcessingJobStatus.QUEUED.value,
    ProcessingJobStatus.RUNNING.value,
    ProcessingJobStatus.PENDING.value,
    ProcessingJobStatus.AWAITING_APPROVAL.value,
}
logger = logging.getLogger(__name__)


def enqueue_document(
    document_id: int,
    *,
    source: str = "manual",
    reason: str | None = None,
    force: bool = False,
) -> Tuple[ProcessingJob, bool]:
    """Create a processing job if needed and push it to the queue."""
    settings = SettingsService().effective_settings()
    queue = get_queue()
    with db_session() as session:
        existing = (
            session.query(ProcessingJob)
            .filter(
                ProcessingJob.document_id == document_id,
                ProcessingJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(ProcessingJob.created_at.desc())
            .first()
        )
        if existing and not force:
            logger.debug(
                "Reusing active job %s for document %s (source=%s)",
                existing.id,
                document_id,
                source,
            )
            return existing, False

        job = ProcessingJob(
            document_id=document_id,
            status=ProcessingJobStatus.QUEUED,
            source=source,
            reason=reason,
            queued_at=datetime.utcnow(),
        )
        session.add(job)
        session.flush()

        doc = session.get(DocumentRecord, document_id) or DocumentRecord(document_id=document_id)
        doc.status = DocumentStatus.QUEUED
        doc.last_error = None
        session.add(doc)

        retry = None
        if settings.job_retry_delays:
            retry = Retry(max=len(settings.job_retry_delays), interval=settings.job_retry_delays)

        job_timeout = int(settings.llm_request_timeout) + 10

        queue.enqueue(
            process_document,
            job.id,
            document_id,
            retry=retry,
            job_timeout=job_timeout,
            description=f"doc:{document_id} src:{source}",
        )

        logger.debug(
            "Enqueued new job %s for document %s source=%s force=%s",
            job.id,
            document_id,
            source,
            force,
        )
        return job, True
