"""Background task entrypoints used by RQ."""

from .services.processing import run_processing_job


def process_document(job_id: int, document_id: int) -> None:
    run_processing_job(job_id, document_id)
