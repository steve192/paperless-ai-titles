from paperless_ai_titles.core.database import db_session
from paperless_ai_titles.core.models import DocumentRecord, ProcessingJob, ProcessingJobStatus
from paperless_ai_titles.services import jobs


class DummyQueue:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def enqueue(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_enqueue_document_creates_job_and_updates_document(monkeypatch):
    fake_queue = DummyQueue()
    monkeypatch.setattr(jobs, "get_queue", lambda: fake_queue)

    job, created = jobs.enqueue_document(101, source="scanner", reason="auto")

    assert created is True
    assert job.document_id == 101
    assert fake_queue.calls
    args, kwargs = fake_queue.calls[0]
    assert args[0].__name__ == "process_document"
    assert kwargs["description"] == "doc:101 src:scanner"
    assert kwargs["job_timeout"] > 0

    with db_session() as session:
        record = session.get(DocumentRecord, 101)
        assert record is not None
        assert record.status == ProcessingJobStatus.QUEUED.value


def test_enqueue_document_reuses_active_job(monkeypatch):
    fake_queue = DummyQueue()
    monkeypatch.setattr(jobs, "get_queue", lambda: fake_queue)
    with db_session() as session:
        existing = ProcessingJob(
            document_id=55,
            status=ProcessingJobStatus.RUNNING.value,
            source="scanner",
        )
        session.add(existing)

    job, created = jobs.enqueue_document(55, source="scanner", reason="auto")

    assert created is False
    assert job.id is not None
    assert fake_queue.calls == []


def test_enqueue_document_force_creates_new_job(monkeypatch):
    fake_queue = DummyQueue()
    monkeypatch.setattr(jobs, "get_queue", lambda: fake_queue)
    with db_session() as session:
        session.add(
            ProcessingJob(
                document_id=77,
                status=ProcessingJobStatus.PENDING.value,
                source="scanner",
            )
        )

    job, created = jobs.enqueue_document(77, source="scanner", reason="retry", force=True)

    assert created is True
    assert job.reason == "retry"
    assert len(fake_queue.calls) == 1
    with db_session() as session:
        record = session.get(DocumentRecord, 77)
        assert record.status == ProcessingJobStatus.QUEUED.value
