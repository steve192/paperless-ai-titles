from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Base declarative model."""


def _build_connection_string(path: str) -> str:
    if path.startswith("sqlite://"):
        return path
    if path == ":memory:":
        return "sqlite://"
    return f"sqlite:///{path}"


def _create_engine():
    settings = get_settings()
    url = _build_connection_string(settings.sqlite_path)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, echo=False, future=True, connect_args=connect_args)


def get_engine():
    return _ENGINE


_ENGINE = _create_engine()
SessionFactory = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False, expire_on_commit=False)


@contextmanager
def db_session() -> Session:
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
