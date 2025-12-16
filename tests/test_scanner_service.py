import types

import pytest

from paperless_ai_titles.core.config import Settings
from paperless_ai_titles.core.database import db_session
from paperless_ai_titles.core.models import DocumentRecord, DocumentStatus
from paperless_ai_titles.services import scanner as scanner_module


class StubSettingsService:
    def __init__(self, settings: Settings, *, needs_onboarding: bool = False):
        self._settings = settings
        self._needs = needs_onboarding

    def effective_settings(self):
        return self._settings

    def needs_onboarding(self):
        return self._needs


def test_needs_worker_pass_respects_finalized_statuses():
    should_enqueue, status = scanner_module._needs_worker_pass(
        1, {1: DocumentStatus.COMPLETED.value}
    )
    assert should_enqueue is False
    assert status == DocumentStatus.COMPLETED.value

    should_enqueue, status = scanner_module._needs_worker_pass(
        2, {2: DocumentStatus.FAILED.value}
    )
    assert should_enqueue is True
    assert status == DocumentStatus.FAILED.value


def test_load_document_status_map_reads_database():
    with db_session() as session:
        session.add(DocumentRecord(document_id=99, status=DocumentStatus.FAILED.value))
    status_map = scanner_module._load_document_status_map([99, 100, None])
    assert status_map[99] == DocumentStatus.FAILED.value
    assert 100 not in status_map


@pytest.mark.asyncio
async def test_run_once_enqueues_only_needed_documents(monkeypatch):
    settings = Settings(scanner_page_size=2, max_jobs_per_scan=5, scanner_enabled=True)
    service = scanner_module.ScannerService()
    service.settings_service = StubSettingsService(settings)

    responses = {
        1: {"results": [{"id": 1}, {"id": 2}], "next": True},
        2: {"results": [{"id": 3}], "next": None},
    }

    async def fake_fetch(self, client, page, page_size):  # noqa: ARG001
        return responses.get(page, {"results": [], "next": None})

    service._fetch_candidates = types.MethodType(fake_fetch, service)

    with db_session() as session:
        session.add(DocumentRecord(document_id=2, status=DocumentStatus.COMPLETED.value))
        session.add(DocumentRecord(document_id=3, status=DocumentStatus.FAILED.value))

    captured: list[tuple[int, dict]] = []

    def fake_enqueue(doc_id, **kwargs):
        captured.append((doc_id, kwargs))

    monkeypatch.setattr(scanner_module, "enqueue_document", fake_enqueue)

    queued = await service.run_once()

    assert queued == 2
    assert [doc_id for doc_id, _ in captured] == [1, 3]
    assert captured[1][1]["reason"].endswith("failed")


@pytest.mark.asyncio
async def test_run_once_skips_when_onboarding_needed(monkeypatch):
    settings = Settings(scanner_enabled=True)
    service = scanner_module.ScannerService()
    service.settings_service = StubSettingsService(settings, needs_onboarding=True)

    async def fake_fetch(self, client, page, page_size):  # pragma: no cover - should not run
        return {"results": [], "next": None}

    service._fetch_candidates = types.MethodType(fake_fetch, service)
    monkeypatch.setattr(scanner_module, "enqueue_document", lambda *args, **kwargs: None)

    queued = await service.run_once()
    assert queued == 0