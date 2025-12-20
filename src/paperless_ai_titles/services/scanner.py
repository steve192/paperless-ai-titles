from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..clients.paperless_client import PaperlessClient
from ..core.status_sets import is_document_finalized
from ..repositories.unit_of_work import UnitOfWork
from ..services.jobs import enqueue_document
from ..services.settings import SettingsService

logger = logging.getLogger(__name__)

@dataclass
class ScannerStatus:
    last_run: Optional[datetime] = None
    last_duration_seconds: Optional[float] = None
    queued_this_run: int = 0
    last_error: Optional[str] = None
    running: bool = False


class ScannerService:
    def __init__(self) -> None:
        self.settings_service = SettingsService()
        self._status = ScannerStatus()
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    def status(self) -> ScannerStatus:
        return self._status

    async def run_once(self) -> int:
        settings = self.settings_service.effective_settings()
        if self.settings_service.needs_onboarding():
            logger.debug("Skipping scan because onboarding not completed")
            return 0
        if not settings.scanner_enabled:
            logger.debug("Scanner disabled via settings")
            return 0

        client = PaperlessClient(settings)
        queued = 0
        page = 1
        while queued < settings.max_jobs_per_scan:
            logger.debug("Scanner fetching page %s (queued=%s)", page, queued)
            payload = await self._fetch_candidates(client, page, settings.scanner_page_size)
            results = payload.get("results", [])
            if not results:
                break
            status_map = _load_document_status_map([doc.get("id") for doc in results])
            for document in results:
                if queued >= settings.max_jobs_per_scan:
                    break
                doc_id = document.get("id")
                if not doc_id:
                    continue
                should_enqueue, existing_status = _needs_worker_pass(doc_id, status_map)
                if not should_enqueue:
                    logger.debug(
                        "Scanner skipping document %s: already finalized with status=%s",
                        doc_id,
                        existing_status,
                    )
                    continue
                reason = "scanner scheduled"
                if existing_status:
                    reason = f"scanner retry from {existing_status}"
                enqueue_document(doc_id, source="scanner", reason=reason)
                queued += 1
                logger.debug("Scanner enqueued document %s (reason=%s)", doc_id, reason)
            if not payload.get("next"):
                break
            page += 1

        logger.info("Scanner enqueued %s document(s)", queued)
        return queued

    async def start(self) -> None:
        if self._task:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if not self._task or not self._stop_event:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        self._stop_event = None

    async def _loop(self) -> None:
        while self._stop_event and not self._stop_event.is_set():
            start = datetime.utcnow()
            queued = 0
            last_error = None
            try:
                self._status.running = True
                queued = await self.run_once()
            except Exception as exc:  # pragma: no cover - background monitoring only
                logger.exception("Scanner run failed")
                last_error = str(exc)
            finally:
                self._status.running = False
            duration = (datetime.utcnow() - start).total_seconds()
            self._status = ScannerStatus(
                last_run=start,
                last_duration_seconds=duration,
                queued_this_run=queued,
                last_error=last_error,
                running=False,
            )
            wait_seconds = self.settings_service.effective_settings().scan_interval_seconds
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                continue

    async def _fetch_candidates(self, client: PaperlessClient, page: int, page_size: int) -> dict[str, Any]:
        settings = self.settings_service.effective_settings()
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "ordering": "-created",
            "expand": "tags,correspondent,document_type,custom_fields",
        }
        logger.debug("Scanner candidate query params: %s", params)
        return await client.list_documents(params)


def _needs_worker_pass(doc_id: int, status_map: dict[int, str | None]) -> tuple[bool, str | None]:
    status = status_map.get(doc_id)
    if not status:
        return True, None
    if is_document_finalized(status):
        return False, status
    return True, status


def _load_document_status_map(document_ids: list[int | None]) -> dict[int, str | None]:
    clean_ids = [doc_id for doc_id in document_ids if isinstance(doc_id, int)]
    if not clean_ids:
        return {}
    with UnitOfWork() as uow:
        return uow.documents.fetch_status_map(clean_ids)


_SCANNER = ScannerService()


def get_scanner_service() -> ScannerService:
    return _SCANNER
