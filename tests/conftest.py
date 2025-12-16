import os
import tempfile

import pytest

TEST_DB = os.path.join(tempfile.gettempdir(), "paperless-ai-titles-tests.db")
os.environ["SQLITE_PATH"] = TEST_DB
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from paperless_ai_titles.core.database import Base, get_engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture(autouse=True)
def reset_database(_prepare_database):
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
