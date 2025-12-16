from __future__ import annotations

from typing import Any, Dict, Optional

from ..core.config import Settings
from ..services.settings import SettingsService
from ..clients.paperless_client import PaperlessClient
from ..services.processing import ProcessingService


class OnboardingService:
    def __init__(self) -> None:
        self.settings_service = SettingsService()

    def state(self) -> dict[str, Any]:
        return {
            "completed": not self.settings_service.needs_onboarding(),
            "defaults": self.settings_service.bootstrap_defaults(),
            "missing_keys": self.settings_service.missing_keys(),
        }

    async def preview_documents(self, overrides: dict[str, Any], page_size: int = 10) -> dict[str, Any]:
        settings = self._temporary_settings(overrides)
        client = PaperlessClient(settings)
        params = {"page_size": page_size, "ordering": "-created", "expand": "tags"}
        return await client.list_documents(params)

    async def dry_run(self, document_id: int, overrides: dict[str, Any]) -> dict[str, Any]:
        settings = self._temporary_settings(overrides)
        service = ProcessingService(settings=settings)
        plan = await service.dry_run(document_id)
        return {
            "document_id": document_id,
            "needs_update": plan.needs_update,
            "reason": plan.reason,
            "existing_title": plan.existing_title,
            "new_title": plan.new_title,
            "evaluation": plan.evaluation.raw if plan.evaluation else None,
            "suggestion": plan.suggestion.raw if plan.suggestion else None,
        }

    def complete(self, overrides: dict[str, Any]) -> None:
        for key, value in overrides.items():
            self.settings_service.save(key, value)
        self.settings_service.mark_onboarding_complete()

    async def load_metadata(self, overrides: dict[str, Any]) -> dict[str, Any]:
        settings = self._temporary_settings(overrides)
        client = PaperlessClient(settings)
        tags = await client.list_tags()
        custom_fields = await client.list_custom_fields()
        return {
            "tags": [
                {"id": tag.get("id"), "name": tag.get("name"), "slug": tag.get("slug")}
                for tag in tags
            ],
            "custom_fields": [
                {
                    "id": field.get("id"),
                    "name": field.get("name"),
                    "slug": field.get("slug"),
                    "data_type": field.get("data_type"),
                }
                for field in custom_fields
            ],
        }

    async def create_tag(self, overrides: dict[str, Any], name: str) -> dict[str, Any]:
        settings = self._temporary_settings(overrides)
        client = PaperlessClient(settings)
        return await client.create_tag(name)

    async def create_custom_field(
        self, overrides: dict[str, Any], name: str, data_type: str = "string"
    ) -> dict[str, Any]:
        settings = self._temporary_settings(overrides)
        client = PaperlessClient(settings)
        return await client.create_custom_field(name, data_type=data_type)

    def _temporary_settings(self, overrides: dict[str, Any]) -> Settings:
        return self.settings_service.effective_settings(extra_overrides=overrides)