from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Tuple

from rq import Retry

from ..core.models import DocumentStatus, ProcessingJobStatus
from ..queue_factory import get_queue
from ..rq_task_handlers import process_document
from ..services.settings import SettingsService
from ..repositories.unit_of_work import UnitOfWork

if TYPE_CHECKING:
    from ..core.models import ProcessingJob

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
    with UnitOfWork() as uow:
        existing = uow.jobs.find_latest_active(document_id)
        if existing and not force:
            logger.debug(
                "Reusing active job %s for document %s (source=%s)",
                existing.id,
                document_id,
                source,
            )
            return existing, False

        job = uow.jobs.create_job(
            document_id=document_id,
            status=ProcessingJobStatus.QUEUED.value,
            source=source,
            reason=reason,
            queued_at=datetime.utcnow(),
        )

        doc = uow.documents.get_or_create(document_id)
        doc.status = DocumentStatus.QUEUED.value
        doc.last_error = None
        uow.documents.add(doc)

        # If we just created a new job while there were other active jobs
        # for this document (e.g. via force reprocess), mark the previous
        # active jobs as skipped so that only the newest job remains
        # active in history.
        previous_active_jobs = uow.jobs.list_active_excluding(document_id, exclude_job_id=job.id)
        for previous in previous_active_jobs:
            logger.debug(
                "Marking previous job %s for document %s as skipped in favor of job %s",
                previous.id,
                document_id,
                job.id,
            )
            previous.status = ProcessingJobStatus.SKIPPED.value
            previous.reason = f"superseded by job {job.id}"
            previous.completed_at = datetime.utcnow()
            uow.jobs.add(previous)

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
