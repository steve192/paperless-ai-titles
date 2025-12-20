from __future__ import annotations

from ..core.database import db_session
from .document_records import DocumentRecordRepository
from .processing_jobs import ProcessingJobRepository
from .settings import SettingsRepository


class UnitOfWork:
    def __init__(self) -> None:
        self._context = None
        self.session = None
        self.documents = None
        self.jobs = None
        self.settings = None

    def __enter__(self) -> "UnitOfWork":
        self._context = db_session()
        self.session = self._context.__enter__()
        self.documents = DocumentRecordRepository(self.session)
        self.jobs = ProcessingJobRepository(self.session)
        self.settings = SettingsRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._context is not None:
            self._context.__exit__(exc_type, exc, traceback)
