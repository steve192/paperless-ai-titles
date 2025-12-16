import json
from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    SettingsConfigDict,
)


class LenientEnvSettingsSource(EnvSettingsSource):
    def decode_complex_value(self, field_name, field, value):  # type: ignore[override]
        try:
            return super().decode_complex_value(field_name, field, value)
        except ValueError:
            return value


class LenientDotEnvSettingsSource(DotEnvSettingsSource):
    def decode_complex_value(self, field_name, field, value):  # type: ignore[override]
        try:
            return super().decode_complex_value(field_name, field, value)
        except ValueError:
            return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            LenientEnvSettingsSource(settings_cls),
            LenientDotEnvSettingsSource(settings_cls),
            file_secret_settings,
        )

    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8080)

    sqlite_path: str = Field(default="/data/paperless_ai_titles.db")
    redis_url: str = Field(default="redis://localhost:6379/0")

    paperless_base_url: AnyHttpUrl = Field(default="https://paperless.example.com")
    paperless_api_token: str = Field(default="changeme")
    paperless_skip_tag: Optional[str] = Field(default=None)
    paperless_require_tag: Optional[str] = Field(default=None)
    paperless_original_title_field: Optional[str] = Field(default="original_title")
    paperless_hook_token: Optional[str] = Field(default=None)

    llm_base_url: AnyHttpUrl = Field(default="http://localhost:8000/v1/chat/completions")
    llm_api_token: str = Field(default="changeme")
    llm_model_name: str = Field(default="local-title-model")
    llm_confidence_threshold: float = Field(default=0.6)
    llm_request_timeout: int = Field(default=300)
    llm_prompt_char_limit: int = Field(default=8000, ge=500)

    auto_apply_titles: bool = Field(default=True)

    scan_interval_seconds: int = Field(default=300, ge=30)
    scanner_page_size: int = Field(default=50)
    scanner_enabled: bool = Field(default=True)
    max_jobs_per_scan: int = Field(default=50)

    ui_default_page_size: int = Field(default=25)

    allowed_origins: List[str] = Field(default_factory=lambda: ["*"])

    log_level: str = Field(default="INFO", description="Root logger level e.g. DEBUG/INFO/WARNING")

    queue_name: str = Field(default="documents")
    job_retry_delays: List[int] = Field(default_factory=lambda: [30, 90, 300])

    @field_validator("job_retry_delays", mode="before")
    @classmethod
    def _parse_job_retry_delays(cls, value):
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            parts = [part.strip() for part in raw.split(",") if part.strip()]
            return [int(part) for part in parts]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
