from __future__ import annotations

from typing import Iterable

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from ..core.models import DocumentRecord, DocumentStatus


class DocumentRecordRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def applied_title_change_condition():
        return and_(
            DocumentRecord.ai_title.is_not(None),
            func.length(func.trim(DocumentRecord.ai_title)) > 0,
        )

    @staticmethod
    def denied_title_change_condition():
        return DocumentRecord.status == DocumentStatus.REJECTED.value

    @staticmethod
    def awaiting_approval_condition():
        return DocumentRecord.status == DocumentStatus.AWAITING_APPROVAL.value

    @staticmethod
    def failed_condition():
        return DocumentRecord.status == DocumentStatus.FAILED.value

    def get(self, document_id: int) -> DocumentRecord | None:
        return self.session.get(DocumentRecord, document_id)

    def get_or_create(self, document_id: int) -> DocumentRecord:
        record = self.get(document_id)
        if record is None:
            record = DocumentRecord(document_id=document_id)
            self.session.add(record)
        return record

    def add(self, record: DocumentRecord) -> None:
        self.session.add(record)

    def list_recent(self, limit: int) -> list[DocumentRecord]:
        stmt = select(DocumentRecord).order_by(DocumentRecord.processed_at.desc()).limit(limit)
        return self.session.execute(stmt).scalars().all()

    def count_awaiting_approval(self) -> int:
        stmt = select(func.count()).select_from(DocumentRecord).where(self.awaiting_approval_condition())
        return self.session.execute(stmt).scalar_one()

    def list_awaiting_approval(self, *, limit: int, offset: int) -> list[DocumentRecord]:
        stmt = (
            select(DocumentRecord)
            .where(self.awaiting_approval_condition())
            .order_by(DocumentRecord.document_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return self.session.execute(stmt).scalars().all()

    def fetch_status_map(self, document_ids: Iterable[int]) -> dict[int, str | None]:
        ids = [doc_id for doc_id in document_ids if isinstance(doc_id, int)]
        if not ids:
            return {}
        stmt = select(DocumentRecord.document_id, DocumentRecord.status).where(
            DocumentRecord.document_id.in_(ids)
        )
        records = self.session.execute(stmt).all()
        return {doc_id: status for doc_id, status in records}

    def filter_ids(
        self,
        document_ids: Iterable[int],
        *,
        exclude_applied: bool = False,
        exclude_denied: bool = False,
    ) -> list[int]:
        ids = [doc_id for doc_id in document_ids if isinstance(doc_id, int)]
        if not ids:
            return []
        if not (exclude_applied or exclude_denied):
            return ids
        conditions = []
        if exclude_applied:
            conditions.append(self.applied_title_change_condition())
        if exclude_denied:
            conditions.append(self.denied_title_change_condition())
        stmt = (
            select(DocumentRecord.document_id)
            .where(DocumentRecord.document_id.in_(ids), or_(*conditions))
        )
        excluded = set(self.session.execute(stmt).scalars().all())
        if not excluded:
            return ids
        return [doc_id for doc_id in ids if doc_id not in excluded]

    def find_ids(
        self,
        *,
        status: str | None = None,
        exclude_applied: bool = False,
        exclude_denied: bool = False,
    ) -> list[int]:
        stmt = select(DocumentRecord.document_id)
        if status:
            stmt = stmt.where(DocumentRecord.status == status)
        if exclude_applied:
            stmt = stmt.where(not_(self.applied_title_change_condition()))
        if exclude_denied:
            stmt = stmt.where(not_(self.denied_title_change_condition()))
        return self.session.execute(stmt).scalars().all()
