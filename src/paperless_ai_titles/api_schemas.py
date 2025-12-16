from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class SettingPayload(BaseModel):
    key: str
    value: Any


class SettingRead(BaseModel):
    key: str
    value: Any

    class Config:
        from_attributes = True


class ProcessingJobRead(BaseModel):
    id: int
    document_id: int
    status: str
    source: str
    reason: Optional[str] = None
    attempt_count: int
    last_error: Optional[str] = None
    queued_at: datetime
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProcessingJobPage(BaseModel):
    items: list[ProcessingJobRead]
    total: int
    page: int
    limit: int


class DocumentRecordRead(BaseModel):
    document_id: int
    original_title: Optional[str]
    ai_title: Optional[str]
    status: str
    confidence: Optional[float]
    lock_reason: Optional[str]
    last_error: Optional[str]
    processed_at: Optional[datetime]
    metadata: Optional[dict] = Field(default=None, alias="extra")

    class Config:
        from_attributes = True
        allow_population_by_field_name = True


class EnqueueRequest(BaseModel):
    document_id: int
    reason: Optional[str] = None


class EnqueueResponse(BaseModel):
    job_id: int
    document_id: int
    status: str


class ForceReprocessRequest(BaseModel):
    document_ids: list[int] | None = None
    include_locked: bool = False
    scope: Literal["selected", "all", "failed"] = "selected"
    respect_existing_titles: bool = False


class HookPayload(BaseModel):
    document_id: int
    token: Optional[str] = None


class QueueMetrics(BaseModel):
    queued: int
    running: int
    failed: int
    completed_today: int
    awaiting_approval: int = 0


class ScanStatus(BaseModel):
    last_run: Optional[datetime]
    last_duration_seconds: Optional[float]
    queued_this_run: int
    last_error: Optional[str] = None


class SetupState(BaseModel):
    completed: bool
    defaults: dict[str, Any]
    missing_keys: list[str]


class SetupSettingsPayload(BaseModel):
    settings: dict[str, Any]


class DryRunRequest(BaseModel):
    document_id: int
    settings: dict[str, Any]


class DryRunResult(BaseModel):
    document_id: int
    needs_update: bool
    reason: str
    existing_title: Optional[str]
    new_title: Optional[str]
    evaluation: Optional[dict]
    suggestion: Optional[dict]


class ApprovalRecord(BaseModel):
    document_id: int
    existing_title: Optional[str]
    suggested_title: str
    reason: str
    confidence: Optional[float]
    created_at: datetime
    ocr_excerpt: Optional[str] = None
    metadata: Optional[dict] = None
    suggestion: Optional[dict] = None
    evaluation: Optional[dict] = None


class ApprovalPage(BaseModel):
    items: list[ApprovalRecord]
    total: int
    page: int
    limit: int


class TagOption(BaseModel):
    id: Optional[int]
    name: str
    slug: Optional[str]


class CustomFieldOption(BaseModel):
    id: Optional[int]
    name: str
    slug: Optional[str]
    data_type: Optional[str]


class SetupMetadata(BaseModel):
    tags: list[TagOption]
    custom_fields: list[CustomFieldOption]


class CreateTagRequest(BaseModel):
    settings: dict[str, Any]
    name: str


class CreateCustomFieldRequest(BaseModel):
    settings: dict[str, Any]
    name: str
    data_type: Optional[str] = "string"
