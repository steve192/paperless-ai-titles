from __future__ import annotations

from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.models import ProcessingJob
from ..core.status_sets import (
    PROCESSING_JOB_ACTIVE_STATUSES,
    PROCESSING_JOB_COMPLETED_STATUSES,
)


class ProcessingJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, job_id: int) -> ProcessingJob | None:
        return self.session.get(ProcessingJob, job_id)

    def add(self, job: ProcessingJob) -> None:
        self.session.add(job)

    def list_recent(self, limit: int) -> list[ProcessingJob]:
        stmt = select(ProcessingJob).order_by(ProcessingJob.created_at.desc()).limit(limit)
        return self.session.execute(stmt).scalars().all()

    def list_history(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        document_id: int | None = None,
        sort_column=None,
        sort_dir: str = "desc",
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[ProcessingJob], int]:
        filters = []
        if status:
            filters.append(ProcessingJob.status == status)
        if source:
            filters.append(func.lower(ProcessingJob.source) == source.lower())
        if document_id is not None:
            filters.append(ProcessingJob.document_id == document_id)
        stmt = select(ProcessingJob)
        if filters:
            stmt = stmt.where(*filters)
        if sort_column is None:
            sort_column = ProcessingJob.created_at
        direction = sort_dir.lower() if sort_dir else "desc"
        order_clause = sort_column.asc() if direction == "asc" else sort_column.desc()
        stmt = stmt.order_by(order_clause, ProcessingJob.id.desc()).limit(limit).offset(offset)
        count_stmt = select(func.count()).select_from(ProcessingJob)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = self.session.execute(count_stmt).scalar_one()
        jobs = self.session.execute(stmt).scalars().all()
        return jobs, total

    def find_latest_active(self, document_id: int) -> ProcessingJob | None:
        stmt = (
            select(ProcessingJob)
            .where(
                ProcessingJob.document_id == document_id,
                ProcessingJob.status.in_(PROCESSING_JOB_ACTIVE_STATUSES),
            )
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def list_active_excluding(
        self,
        document_id: int,
        *,
        exclude_job_id: int | None = None,
    ) -> list[ProcessingJob]:
        stmt = select(ProcessingJob).where(
            ProcessingJob.document_id == document_id,
            ProcessingJob.status.in_(PROCESSING_JOB_ACTIVE_STATUSES),
        )
        if exclude_job_id is not None:
            stmt = stmt.where(ProcessingJob.id != exclude_job_id)
        return self.session.execute(stmt).scalars().all()

    def create_job(
        self,
        *,
        document_id: int,
        status: str,
        source: str,
        reason: str | None = None,
        queued_at: datetime | None = None,
    ) -> ProcessingJob:
        job = ProcessingJob(
            document_id=document_id,
            status=status,
            source=source,
            reason=reason,
            queued_at=queued_at or datetime.utcnow(),
        )
        self.session.add(job)
        self.session.flush()
        return job

    def status_counts(self) -> dict[str, int]:
        counts = self.session.execute(
            select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)
        ).all()
        return {status: total for status, total in counts}

    def count_completed_since(self, since: datetime) -> int:
        stmt = select(func.count()).where(
            ProcessingJob.status.in_(PROCESSING_JOB_COMPLETED_STATUSES),
            ProcessingJob.completed_at >= since,
        )
        return self.session.execute(stmt).scalar_one()
