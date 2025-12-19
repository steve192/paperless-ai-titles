from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from ..core.config import Settings, get_settings
from ..core.database import db_session
from ..core.models import Setting

CONFIGURABLE_KEYS = {
    "paperless_base_url",
    "paperless_api_token",
    "paperless_skip_tag",
    "paperless_require_tag",
    "paperless_original_title_field",
    "paperless_hook_token",
    "llm_base_url",
    "llm_api_token",
    "llm_model_name",
    "llm_confidence_threshold",
    "llm_request_timeout",
    "llm_prompt_char_limit",
    "llm_use_custom_prompt",
    "llm_custom_prompt",
    "auto_apply_titles",
    "scan_interval_seconds",
    "scanner_page_size",
    "max_jobs_per_scan",
}

REQUIRED_KEYS = {
    "paperless_base_url",
    "paperless_api_token",
    "llm_base_url",
    "llm_api_token",
}

ONBOARDING_FLAG = "onboarding_completed"


class SettingsService:
    """Reads and persists runtime configuration overrides."""

    def __init__(self) -> None:
        self._base = get_settings()

    def list_entries(self) -> list[Setting]:
        with db_session() as session:
            stmt = select(Setting).order_by(Setting.key)
            return session.execute(stmt).scalars().all()

    def save(self, key: str, value: Any) -> Setting:
        if key not in CONFIGURABLE_KEYS and key != ONBOARDING_FLAG:
            raise ValueError(f"Key '{key}' is not configurable")
        text_value = str(value)
        with db_session() as session:
            entry = session.get(Setting, key)
            if entry is None:
                entry = Setting(key=key, value=text_value)
            else:
                entry.value = text_value
            session.add(entry)
            session.flush()
            session.refresh(entry)
            return entry

    def delete(self, key: str) -> None:
        with db_session() as session:
            entry = session.get(Setting, key)
            if entry:
                session.delete(entry)

    def overrides(self) -> Dict[str, Any]:
        entries = self.list_entries()
        return {entry.key: entry.value for entry in entries if entry.key != ONBOARDING_FLAG}

    def effective_settings(self, extra_overrides: Optional[dict[str, Any]] = None) -> Settings:
        data = self._base.model_dump()
        data.update(self.overrides())
        if extra_overrides:
            data.update(extra_overrides)
        return Settings(**data)

    def iter_effective_pairs(self, keys: Iterable[str] | None = None) -> List[tuple[str, Any]]:
        settings = self.effective_settings()
        payload = settings.model_dump()
        selected = keys or CONFIGURABLE_KEYS
        return [(key, payload.get(key)) for key in selected]

    def bootstrap_defaults(self) -> dict[str, Any]:
        defaults = {}
        for key in CONFIGURABLE_KEYS:
            if hasattr(self._base, key):
                defaults[key] = getattr(self._base, key)
        defaults.update(self.overrides())
        return defaults

    def onboarding_completed(self) -> bool:
        with db_session() as session:
            flag = session.get(Setting, ONBOARDING_FLAG)
            if flag is None:
                return False
            return flag.value.lower() in {"1", "true", "yes"}

    def mark_onboarding_complete(self) -> None:
        self.save(ONBOARDING_FLAG, "true")

    def reset_onboarding(self) -> None:
        self.delete(ONBOARDING_FLAG)

    def needs_onboarding(self) -> bool:
        if not self.onboarding_completed():
            return True
        effective = self.effective_settings().model_dump()
        return any(not effective.get(key) for key in REQUIRED_KEYS)

    def missing_keys(self) -> list[str]:
        effective = self.effective_settings().model_dump()
        return [key for key in REQUIRED_KEYS if not effective.get(key)]
