from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..clients.llm_client import TitleEvaluation, TitleLLMClient, TitleSuggestion
from ..clients.paperless_client import PaperlessClient
from ..core.database import db_session
from ..core.models import (
    DocumentRecord,
    DocumentStatus,
    ProcessingJob,
    ProcessingJobStatus,
)
from ..document_eligibility import document_has_original_title_field, document_passes_tag_filters
from ..services.settings import SettingsService

logger = logging.getLogger(__name__)


@dataclass
class ProcessingPlan:
    document_id: int
    needs_update: bool
    reason: str
    existing_title: str
    new_title: Optional[str]
    suggestion: Optional[TitleSuggestion]
    evaluation: Optional[TitleEvaluation]
    document_payload: dict[str, Any]
    ocr_text: str


class ProcessingService:
    def __init__(self, settings_service: SettingsService | None = None, settings=None) -> None:
        self.settings_service = settings_service or SettingsService()
        self.settings = settings or self.settings_service.effective_settings()
        self.paperless = PaperlessClient(self.settings)
        self.llm = TitleLLMClient(self.settings)

    async def build_plan(self, document_id: int) -> ProcessingPlan:
        logger.debug("Building processing plan for document %s", document_id)
        expand = "tags,correspondent,document_type,custom_fields"
        document = await self.paperless.fetch_document(document_id, expand=expand)
        passes_filters, reason = document_passes_tag_filters(document, settings=self.settings)
        existing_title = (document.get("title") or "").strip()
        ocr_text = (document.get("content") or "").strip()
        logger.debug(
            "Document %s eligibility result: passes=%s reason=%s existing_title='%s'",
            document_id,
            passes_filters,
            reason,
            existing_title,
        )
        evaluation: TitleEvaluation | None = None
        if document_has_original_title_field(document, self.settings):
            logger.debug("Document %s already has original title field; skipping", document_id)
            return ProcessingPlan(
                document_id=document_id,
                needs_update=False,
                reason="Original title already captured",
                existing_title=existing_title,
                new_title=None,
                suggestion=None,
                evaluation=evaluation,
                document_payload=document,
                ocr_text=ocr_text,
            )
        if not ocr_text:
            logger.debug("Document %s has no OCR text; skipping", document_id)
            return ProcessingPlan(
                document_id=document_id,
                needs_update=False,
                reason="Document has no OCR text content",
                existing_title=existing_title,
                new_title=None,
                suggestion=None,
                evaluation=evaluation,
                document_payload=document,
                ocr_text=ocr_text,
            )
        if not passes_filters:
            logger.debug("Document %s failed tag filters: %s", document_id, reason)
            return ProcessingPlan(
                document_id=document_id,
                needs_update=False,
                reason=reason,
                existing_title=existing_title,
                new_title=None,
                suggestion=None,
                evaluation=evaluation,
                document_payload=document,
                ocr_text=ocr_text,
            )

        update_reason = "llm_suggestion"
        if existing_title:
            logger.debug("Evaluating existing title for document %s", document_id)
            evaluation = await self.llm.evaluate_title(existing_title, ocr_text)
            logger.debug(
                "Title evaluation for document %s acceptable=%s confidence=%s",
                document_id,
                evaluation.acceptable,
                evaluation.confidence,
            )
            if evaluation.acceptable:
                return ProcessingPlan(
                    document_id=document_id,
                    needs_update=False,
                    reason="LLM approved existing title",
                    existing_title=existing_title,
                    new_title=None,
                    suggestion=None,
                    evaluation=evaluation,
                    document_payload=document,
                    ocr_text=ocr_text,
                )
            update_reason = "LLM flagged existing title"
        else:
            update_reason = "missing existing title"

        suggestion = await self.llm.propose_title(ocr_text, metadata=document)
        new_title = suggestion.title.strip()
        logger.debug(
            "LLM suggestion for document %s new_title='%s' confidence=%s",
            document_id,
            new_title,
            suggestion.confidence,
        )
        if not new_title:
            raise ValueError("LLM returned an empty title")

        return ProcessingPlan(
            document_id=document_id,
            needs_update=True,
            reason=update_reason,
            existing_title=existing_title,
            new_title=new_title,
            suggestion=suggestion,
            evaluation=evaluation,
            document_payload=document,
            ocr_text=ocr_text,
        )

    async def run_job(self, job_id: int, document_id: int) -> None:
        logger.debug("Starting processing job %s for document %s", job_id, document_id)
        plan = await self.build_plan(document_id)
        if not plan.needs_update:
            logger.debug("Job %s skipped: %s", job_id, plan.reason)
            self._mark_skipped(job_id, document_id, plan.reason)
            return
        confidence_ok = self._confidence_sufficient(plan)
        if self.settings.auto_apply_titles and confidence_ok:
            logger.debug("Applying plan automatically for job %s", job_id)
            await self._apply_plan(job_id, plan)
        else:
            if self.settings.auto_apply_titles and not confidence_ok and plan.suggestion and plan.suggestion.confidence is not None:
                logger.debug(
                    "Suggestion confidence %.3f below threshold %.3f for document %s; routing to approval",
                    plan.suggestion.confidence,
                    self.settings.llm_confidence_threshold,
                    plan.document_id,
                )
                plan.reason = self._low_confidence_reason(plan)
            logger.debug("Storing pending plan for manual approval job %s", job_id)
            self._store_pending_plan(job_id, plan)

    async def dry_run(self, document_id: int) -> ProcessingPlan:
        return await self.build_plan(document_id)

    async def _apply_plan(self, job_id: int | None, plan: ProcessingPlan) -> None:
        assert plan.new_title, "plan missing new title"
        logger.debug("Updating title for document %s", plan.document_id)
        await self.paperless.update_title(plan.document_id, plan.new_title)
        if self.settings.paperless_original_title_field:
            placeholder = "[empty before AI]"
            original_value = plan.existing_title or plan.document_payload.get("title") or placeholder
            try:
                logger.debug(
                    "Recording original title for document %s into custom field '%s'",
                    plan.document_id,
                    self.settings.paperless_original_title_field,
                )
                await self.paperless.set_custom_field(
                    plan.document_id,
                    self.settings.paperless_original_title_field,
                    original_value,
                )
            except Exception:  # pragma: no cover - optional metadata best effort
                logger.warning("Could not set custom field for document %s", plan.document_id, exc_info=True)

        with db_session() as session:
            record = session.get(DocumentRecord, plan.document_id) or DocumentRecord(document_id=plan.document_id)
            if not record.original_title:
                record.original_title = plan.existing_title or plan.new_title
            record.ai_title = plan.new_title
            record.status = DocumentStatus.COMPLETED.value
            record.confidence = plan.suggestion.confidence if plan.suggestion else None
            record.last_error = None
            record.processed_at = datetime.utcnow()
            record.lock_reason = None
            self._apply_metadata(record, plan)
            session.add(record)
            logger.debug(
                "Document %s metadata updated; job_id=%s",
                plan.document_id,
                job_id,
            )

            if job_id is not None:
                job = session.get(ProcessingJob, job_id)
            else:
                job = None
            if job:
                job.status = ProcessingJobStatus.COMPLETED.value
                job.completed_at = datetime.utcnow()
                job.llm_response = plan.suggestion.raw if plan.suggestion else None
                job.reason = plan.reason
                session.add(job)
                logger.debug("Job %s marked completed", job_id)

    def _store_pending_plan(self, job_id: int, plan: ProcessingPlan) -> None:
        assert plan.new_title, "plan missing new title"
        logger.debug("Persisting pending plan for document %s job %s", plan.document_id, job_id)
        pending_snapshot = {
            "job_id": job_id,
            "new_title": plan.new_title,
            "reason": plan.reason,
            "confidence": plan.suggestion.confidence if plan.suggestion else None,
            "suggestion": plan.suggestion.raw if plan.suggestion else None,
            "evaluation": plan.evaluation.raw if plan.evaluation else None,
            "existing_title": plan.existing_title,
            "ocr_excerpt": plan.ocr_text[:400].strip(),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        with db_session() as session:
            record = session.get(DocumentRecord, plan.document_id) or DocumentRecord(document_id=plan.document_id)
            if not record.original_title:
                record.original_title = plan.existing_title or plan.document_payload.get("title") or plan.new_title
            record.ai_title = None
            record.status = DocumentStatus.AWAITING_APPROVAL.value
            record.confidence = plan.suggestion.confidence if plan.suggestion else None
            record.last_error = None
            record.processed_at = None
            record.lock_reason = plan.reason
            self._apply_metadata(record, plan, pending_snapshot)
            session.add(record)
            logger.debug("Document %s awaiting approval", plan.document_id)

            job = session.get(ProcessingJob, job_id)
            if job:
                job.status = ProcessingJobStatus.AWAITING_APPROVAL.value
                job.reason = plan.reason
                job.llm_response = plan.suggestion.raw if plan.suggestion else None
                job.completed_at = datetime.utcnow()
                session.add(job)
                logger.debug("Job %s set to awaiting approval", job_id)

    def _apply_metadata(
        self,
        record: DocumentRecord,
        plan: ProcessingPlan,
        pending_snapshot: dict[str, Any] | None = None,
    ) -> None:
        metadata = dict(record.extra or {})
        metadata.update(
            {
                "tags": _serialize_tags(plan.document_payload.get("tags", [])),
                "correspondent": plan.document_payload.get("correspondent"),
            }
        )
        if pending_snapshot:
            metadata["pending"] = pending_snapshot
        else:
            metadata.pop("pending", None)
        record.extra = metadata

    def _load_pending_payload(self, document_id: int) -> dict[str, Any]:
        with db_session() as session:
            record = session.get(DocumentRecord, document_id)
            if not record or record.status != DocumentStatus.AWAITING_APPROVAL.value:
                raise ValueError("Document is not awaiting approval")
            pending = (record.extra or {}).get("pending")  # type: ignore[index]
            if not isinstance(pending, dict):
                raise ValueError("Pending metadata missing for document")
            return dict(pending)

    async def approve_pending(self, document_id: int) -> None:
        pending = self._load_pending_payload(document_id)
        job_id = pending.get("job_id")
        new_title = pending.get("new_title")
        if not new_title:
            raise ValueError("Pending suggestion missing new title")
        document = await self.paperless.fetch_document(
            document_id,
            expand="tags,correspondent,document_type,custom_fields",
        )
        logger.debug("Approving pending plan for document %s", document_id)
        suggestion = TitleSuggestion(
            title=new_title,
            raw=pending.get("suggestion") or {},
            confidence=pending.get("confidence"),
        )
        plan = ProcessingPlan(
            document_id=document_id,
            needs_update=True,
            reason=pending.get("reason", "manual approval"),
            existing_title=pending.get("existing_title") or (document.get("title") or ""),
            new_title=new_title,
            suggestion=suggestion,
            evaluation=None,
            document_payload=document,
            ocr_text=(document.get("content") or ""),
        )
        await self._apply_plan(job_id, plan)

    def deny_pending(self, document_id: int, reason: str | None = None) -> None:
        pending = self._load_pending_payload(document_id)
        job_id = pending.get("job_id")
        denial_reason = reason or "denied by reviewer"
        logger.debug("Denying pending plan for document %s reason=%s", document_id, denial_reason)
        with db_session() as session:
            record = session.get(DocumentRecord, document_id)
            if not record:
                raise ValueError("Document record missing")
            record.ai_title = None
            record.status = DocumentStatus.REJECTED.value
            record.lock_reason = denial_reason
            record.last_error = None
            record.processed_at = datetime.utcnow()
            metadata = dict(record.extra or {})
            metadata.pop("pending", None)
            record.extra = metadata
            session.add(record)

            if job_id:
                job = session.get(ProcessingJob, job_id)
                if job:
                    job.status = ProcessingJobStatus.REJECTED.value
                    job.reason = denial_reason
                    job.completed_at = datetime.utcnow()
                    session.add(job)
                    logger.debug("Job %s marked rejected", job_id)

    def _mark_skipped(self, job_id: int, document_id: int, reason: str) -> None:
        logger.debug("Marking job %s skipped for document %s: %s", job_id, document_id, reason)
        with db_session() as session:
            job = session.get(ProcessingJob, job_id)
            if job:
                job.status = ProcessingJobStatus.SKIPPED.value
                job.reason = reason
                job.completed_at = datetime.utcnow()
                session.add(job)
            record = session.get(DocumentRecord, document_id) or DocumentRecord(document_id=document_id)
            record.status = DocumentStatus.SKIPPED.value
            record.lock_reason = reason
            record.processed_at = datetime.utcnow()
            metadata = dict(record.extra or {})
            metadata.pop("pending", None)
            record.extra = metadata
            session.add(record)

    def mark_failure(self, job_id: int, document_id: int, error: str) -> None:
        logger.debug("Marking job %s failed for document %s error=%s", job_id, document_id, error)
        with db_session() as session:
            summary = (error or "").strip().splitlines()[0] or "error"
            if len(summary) > 240:
                summary = summary[:237] + "..."
            job = session.get(ProcessingJob, job_id)
            if job:
                job.status = ProcessingJobStatus.FAILED.value
                job.last_error = error
                job.reason = summary
                session.add(job)
            record = session.get(DocumentRecord, document_id) or DocumentRecord(document_id=document_id)
            record.status = DocumentStatus.FAILED.value
            record.last_error = error
            record.processed_at = datetime.utcnow()
            metadata = dict(record.extra or {})
            metadata.pop("pending", None)
            record.extra = metadata
            session.add(record)

    def _confidence_sufficient(self, plan: ProcessingPlan) -> bool:
        if not plan.suggestion or plan.suggestion.confidence is None:
            return True
        return plan.suggestion.confidence >= self.settings.llm_confidence_threshold

    def _low_confidence_reason(self, plan: ProcessingPlan) -> str:
        if not plan.suggestion or plan.suggestion.confidence is None:
            return plan.reason
        return (
            f"{plan.reason} (confidence {plan.suggestion.confidence:.2f} < "
            f"threshold {self.settings.llm_confidence_threshold:.2f})"
        )


def _serialize_tags(tags: Any) -> list[str]:
    serialized: list[str] = []
    for tag in tags or []:
        if isinstance(tag, dict) and tag.get("slug"):
            serialized.append(tag["slug"])
    return serialized


def run_processing_job(job_id: int, document_id: int) -> None:
    service = ProcessingService()
    _set_job_running(job_id)
    try:
        logger.debug("Worker executing job %s for document %s", job_id, document_id)
        asyncio.run(service.run_job(job_id, document_id))
    except Exception as exc:  # pragma: no cover - worker logging only
        service.mark_failure(job_id, document_id, str(exc))
        raise


def _set_job_running(job_id: int) -> None:
    with db_session() as session:
        job = session.get(ProcessingJob, job_id)
        if job:
            job.status = ProcessingJobStatus.RUNNING.value
            job.attempt_count = (job.attempt_count or 0) + 1
            session.add(job)