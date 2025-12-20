from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Setting


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_entries(self) -> list[Setting]:
        stmt = select(Setting).order_by(Setting.key)
        return self.session.execute(stmt).scalars().all()

    def get(self, key: str) -> Setting | None:
        return self.session.get(Setting, key)

    def save(self, key: str, value: str) -> Setting:
        entry = self.get(key)
        if entry is None:
            entry = Setting(key=key, value=value)
        else:
            entry.value = value
        self.session.add(entry)
        self.session.flush()
        self.session.refresh(entry)
        return entry

    def delete(self, key: str) -> None:
        entry = self.get(key)
        if entry:
            self.session.delete(entry)
