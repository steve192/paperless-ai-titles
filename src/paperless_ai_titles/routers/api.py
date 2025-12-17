from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, or_, select

from ..core.database import db_session
from ..core.models import (
    DocumentRecord,
    DocumentStatus,
    ProcessingJob,
    ProcessingJobStatus,
)
from ..api_schemas import (
    CreateCustomFieldRequest,
    CreateTagRequest,
    CustomFieldOption,
    DocumentRecordRead,
    DryRunRequest,
    DryRunResult,
    EnqueueRequest,
    EnqueueResponse,
    ForceReprocessRequest,
    HookPayload,
    ProcessingJobRead,
    ProcessingJobPage,
    ApprovalRecord,
    ApprovalPage,
    QueueMetrics,
    ScanStatus,
    SettingPayload,
    SettingRead,
    SetupMetadata,
    SetupSettingsPayload,
    SetupState,
    TagOption,
)
from ..services.jobs import enqueue_document
from ..services.onboarding import OnboardingConnectionError, OnboardingService
from ..services.processing import ProcessingService
from ..services.scanner import get_scanner_service
from ..services.settings import CONFIGURABLE_KEYS, SettingsService

router = APIRouter(prefix="/api", tags=["api"])
settings_service = SettingsService()
scanner_service = get_scanner_service()

JOB_SORT_COLUMNS = {
    "created_at": ProcessingJob.created_at,
    "updated_at": ProcessingJob.updated_at,
    "queued_at": ProcessingJob.queued_at,
    "completed_at": ProcessingJob.completed_at,
}

DEFAULT_JOB_SORT = "created_at"


def _parse_timestamp(raw: str | None) -> datetime:
    if not raw:
        return datetime.utcnow()
    cleaned = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.utcnow()


def _onboarding_error_to_http(exc: OnboardingConnectionError) -> HTTPException:
    detail = {
        "service": exc.service,
        "status_code": exc.status_code,
        "url": exc.url,
        "message": exc.message,
    }
    # Use 502 Bad Gateway to signal an upstream dependency failure during
    # onboarding, while surfacing rich context for the UI.
    raise HTTPException(status_code=502, detail=detail)


@router.get("/settings", response_model=list[SettingRead])
def list_effective_settings() -> list[SettingRead]:
    pairs = settings_service.iter_effective_pairs(sorted(CONFIGURABLE_KEYS))
    return [SettingRead(key=key, value=value) for key, value in pairs]


@router.post("/settings", response_model=SettingRead)
def update_setting(payload: SettingPayload) -> SettingRead:
    entry = settings_service.save(payload.key, payload.value)
    return SettingRead(key=entry.key, value=entry.value)


@router.get("/jobs", response_model=list[ProcessingJobRead])
def list_jobs(limit: int = 25) -> list[ProcessingJobRead]:
    with db_session() as session:
        stmt = select(ProcessingJob).order_by(ProcessingJob.created_at.desc()).limit(limit)
        jobs = session.execute(stmt).scalars().all()
        return jobs


@router.get("/jobs/history", response_model=ProcessingJobPage)
def job_history(
    page: int = 1,
    limit: int = 25,
    status: str | None = None,
    source: str | None = None,
    document_id: int | None = None,
    sort_by: str = DEFAULT_JOB_SORT,
    sort_dir: str = "desc",
) -> ProcessingJobPage:
    limit = max(1, min(limit, 100))
    page = max(1, page)
    filters: list = []
    if status:
        normalized_status = status.lower()
        valid_statuses = {value.value for value in ProcessingJobStatus}
        if normalized_status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Invalid job status filter")
        filters.append(ProcessingJob.status == normalized_status)
    if source:
        filters.append(func.lower(ProcessingJob.source) == source.lower())
    if document_id is not None:
        if document_id <= 0:
            raise HTTPException(status_code=400, detail="document_id must be positive")
        filters.append(ProcessingJob.document_id == document_id)
    sort_key = (sort_by or DEFAULT_JOB_SORT).lower()
    column = JOB_SORT_COLUMNS.get(sort_key)
    if column is None:
        raise HTTPException(status_code=400, detail="Unsupported sort column")
    direction = (sort_dir or "desc").lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="sort_dir must be 'asc' or 'desc'")
    order_clause = column.asc() if direction == "asc" else column.desc()
    offset = (page - 1) * limit
    with db_session() as session:
        stmt = select(ProcessingJob)
        if filters:
            stmt = stmt.where(*filters)
        stmt = stmt.order_by(order_clause, ProcessingJob.id.desc()).limit(limit).offset(offset)
        jobs = session.execute(stmt).scalars().all()
        count_stmt = select(func.count()).select_from(ProcessingJob)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = session.execute(count_stmt).scalar_one()
    return ProcessingJobPage(items=jobs, total=total, page=page, limit=limit)


@router.get("/documents", response_model=list[DocumentRecordRead])
def list_documents(limit: int = 25) -> list[DocumentRecordRead]:
    with db_session() as session:
        stmt = select(DocumentRecord).order_by(DocumentRecord.processed_at.desc()).limit(limit)
        records = session.execute(stmt).scalars().all()
        return records


@router.post("/enqueue", response_model=EnqueueResponse)
def enqueue_endpoint(request: EnqueueRequest) -> EnqueueResponse:
    job, _ = enqueue_document(request.document_id, source="api", reason=request.reason)
    return EnqueueResponse(job_id=job.id, document_id=job.document_id, status=job.status)


@router.post("/force-reprocess")
def force_reprocess(request: ForceReprocessRequest) -> dict:
    def _missing_original_title_condition():
        return or_(
            DocumentRecord.original_title.is_(None),
            func.length(func.trim(DocumentRecord.original_title)) == 0,
        )

    scope = request.scope or "selected"
    doc_ids = [doc_id for doc_id in (request.document_ids or []) if doc_id and doc_id > 0]
    deduped: list[int] = []
    seen: set[int] = set()
    for doc_id in doc_ids:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        deduped.append(doc_id)
    doc_ids = deduped
    if not doc_ids and scope == "selected":
        scope = "all"

    if doc_ids and request.respect_existing_titles:
        with db_session() as session:
            stmt = (
                select(DocumentRecord.document_id)
                .where(
                    DocumentRecord.document_id.in_(doc_ids),
                    _missing_original_title_condition(),
                )
            )
            doc_ids = session.execute(stmt).scalars().all()

    if not doc_ids:
        with db_session() as session:
            stmt = select(DocumentRecord.document_id)
            filters = []
            if scope == "failed":
                filters.append(DocumentRecord.status == DocumentStatus.FAILED.value)
            if request.respect_existing_titles:
                filters.append(_missing_original_title_condition())
            if filters:
                stmt = stmt.where(*filters)
            doc_ids = session.execute(stmt).scalars().all()

    if not doc_ids:
        raise HTTPException(status_code=400, detail="No documents matched reprocess criteria")

    for doc_id in doc_ids:
        enqueue_document(doc_id, source="force-reprocess", reason="force", force=True)
    return {"queued": len(doc_ids)}


@router.post("/hooks/paperless", response_model=EnqueueResponse)
def paperless_hook(payload: HookPayload) -> EnqueueResponse:
    settings = settings_service.effective_settings()
    if settings.paperless_hook_token and payload.token != settings.paperless_hook_token:
        raise HTTPException(status_code=401, detail="Invalid hook token")
    job, _ = enqueue_document(payload.document_id, source="hook", reason="paperless hook")
    return EnqueueResponse(job_id=job.id, document_id=job.document_id, status=job.status)


@router.get("/queue/metrics", response_model=QueueMetrics)
def queue_metrics() -> QueueMetrics:
    with db_session() as session:
        counts = session.execute(
            select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)
        ).all()
        count_map = {status: total for status, total in counts}
        start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        completed_today = session.execute(
            select(func.count()).where(
                ProcessingJob.status == ProcessingJobStatus.COMPLETED.value,
                ProcessingJob.completed_at >= start_of_day,
            )
        ).scalar_one()
        return QueueMetrics(
            queued=count_map.get(ProcessingJobStatus.QUEUED.value, 0),
            running=count_map.get(ProcessingJobStatus.RUNNING.value, 0),
            failed=count_map.get(ProcessingJobStatus.FAILED.value, 0),
            completed_today=completed_today,
            awaiting_approval=count_map.get(ProcessingJobStatus.AWAITING_APPROVAL.value, 0),
        )


@router.get("/scan/status", response_model=ScanStatus)
def scan_status() -> ScanStatus:
    status = scanner_service.status()
    return ScanStatus(
        last_run=status.last_run,
        last_duration_seconds=status.last_duration_seconds,
        queued_this_run=status.queued_this_run,
        last_error=status.last_error,
    )


@router.get("/setup/state", response_model=SetupState)
def setup_state() -> SetupState:
    return SetupState(**OnboardingService().state())


@router.post("/setup/preview-documents")
async def setup_preview(payload: SetupSettingsPayload) -> dict:
    try:
        return await OnboardingService().preview_documents(payload.settings)
    except OnboardingConnectionError as exc:
        raise _onboarding_error_to_http(exc)


@router.post("/setup/metadata", response_model=SetupMetadata)
async def setup_metadata(payload: SetupSettingsPayload) -> SetupMetadata:
    data = await OnboardingService().load_metadata(payload.settings)
    return SetupMetadata(**data)


@router.post("/setup/dry-run", response_model=DryRunResult)
async def setup_dry_run(payload: DryRunRequest) -> DryRunResult:
    try:
        result = await OnboardingService().dry_run(payload.document_id, payload.settings)
    except OnboardingConnectionError as exc:
        raise _onboarding_error_to_http(exc)
    return DryRunResult(**result)


@router.post("/setup/complete")
async def setup_complete(payload: SetupSettingsPayload) -> dict:
    service = OnboardingService()
    # Require successful connectivity to both Paperless and the LLM before
    # enabling automation.
    try:
        await service.validate_connections(payload.settings)
    except OnboardingConnectionError as exc:
        raise _onboarding_error_to_http(exc)
    service.complete(payload.settings)
    await scanner_service.start()
    return {"status": "ok"}


@router.get("/approvals", response_model=ApprovalPage)
def approvals(limit: int = 25, page: int = 1) -> ApprovalPage:
    limit = max(1, min(limit, 100))
    page = max(1, page)
    offset = (page - 1) * limit
    with db_session() as session:
        base_stmt = select(DocumentRecord).where(
            DocumentRecord.status == DocumentStatus.AWAITING_APPROVAL.value
        )
        count_stmt = (
            select(func.count())
            .select_from(DocumentRecord)
            .where(DocumentRecord.status == DocumentStatus.AWAITING_APPROVAL.value)
        )
        total = session.execute(count_stmt).scalar_one()
        stmt = base_stmt.order_by(DocumentRecord.document_id.desc()).limit(limit).offset(offset)
        records = session.execute(stmt).scalars().all()
    approvals: list[ApprovalRecord] = []
    for record in records:
        pending = (record.extra or {}).get("pending") if record.extra else None
        if not isinstance(pending, dict):
            continue
        suggested_title = pending.get("new_title")
        if not suggested_title:
            continue
        metadata = dict(record.extra or {})
        metadata.pop("pending", None)
        approvals.append(
            ApprovalRecord(
                document_id=record.document_id,
                existing_title=pending.get("existing_title") or record.original_title,
                suggested_title=suggested_title,
                reason=pending.get("reason") or record.lock_reason or "awaiting approval",
                confidence=pending.get("confidence"),
                created_at=_parse_timestamp(pending.get("created_at")),
                ocr_excerpt=pending.get("ocr_excerpt"),
                metadata=metadata or None,
                suggestion=pending.get("suggestion"),
                evaluation=pending.get("evaluation"),
            )
        )
    return ApprovalPage(items=approvals, total=total, page=page, limit=limit)


@router.post("/approvals/{document_id}/approve")
async def approve(document_id: int) -> dict:
    service = ProcessingService()
    try:
        await service.approve_pending(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}


@router.post("/approvals/{document_id}/deny")
async def deny(document_id: int) -> dict:
    service = ProcessingService()
    try:
        service.deny_pending(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}


@router.post("/setup/create-tag", response_model=TagOption)
async def setup_create_tag(payload: CreateTagRequest) -> TagOption:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required")
    result = await OnboardingService().create_tag(payload.settings, name)
    return TagOption(id=result.get("id"), name=result.get("name"), slug=result.get("slug"))


@router.post("/setup/create-custom-field", response_model=CustomFieldOption)
async def setup_create_custom_field(payload: CreateCustomFieldRequest) -> CustomFieldOption:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Custom field name is required")
    data_type = payload.data_type or "string"
    result = await OnboardingService().create_custom_field(payload.settings, name, data_type=data_type)
    return CustomFieldOption(
        id=result.get("id"),
        name=result.get("name"),
        slug=result.get("slug"),
        data_type=result.get("data_type"),
    )
