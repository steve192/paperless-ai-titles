from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from ..core.config import Settings
from ..services.settings import SettingsService
from ..clients.paperless_client import PaperlessClient
from ..clients.llm_client import TitleLLMClient
from ..services.processing import ProcessingService


class OnboardingConnectionError(Exception):
    def __init__(
        self,
        service: str,
        *,
        url: Optional[str] = None,
        status_code: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        self.service = service
        self.url = url
        self.status_code = status_code
        self.message = message or "Connection to upstream service failed"
        super().__init__(self.message)


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
        try:
            return await client.list_documents(params)
        except httpx.HTTPError as exc:  # pragma: no cover - network boundary
            raise _wrap_http_error("paperless", exc) from exc

    async def dry_run(self, document_id: int, overrides: dict[str, Any]) -> dict[str, Any]:
        settings = self._temporary_settings(overrides)
        service = ProcessingService(settings=settings)
        try:
            plan = await service.dry_run(document_id)
        except httpx.HTTPError as exc:  # pragma: no cover - network boundary
            # Could be Paperless or LLM; the URL in the exception will clarify.
            raise _wrap_http_error("upstream", exc) from exc
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

    async def validate_connections(self, overrides: dict[str, Any]) -> None:
        """Ensure both Paperless and LLM are reachable with the provided overrides.

        This is used by onboarding completion to avoid enabling automation when
        either upstream dependency is misconfigured.
        """
        settings = self._temporary_settings(overrides)
        await _validate_paperless(settings)
        await _validate_llm(settings)

    def _temporary_settings(self, overrides: dict[str, Any]) -> Settings:
        return self.settings_service.effective_settings(extra_overrides=overrides)


def _wrap_http_error(service: str, exc: httpx.HTTPError) -> OnboardingConnectionError:
    response = getattr(exc, "response", None)
    request = getattr(exc, "request", None)
    status_code: Optional[int] = getattr(response, "status_code", None)
    url: Optional[str] = None
    if request is not None and getattr(request, "url", None) is not None:
        url = str(request.url)
    message = str(exc)
    return OnboardingConnectionError(
        service,
        url=url,
        status_code=status_code,
        message=message,
    )


async def _ping_paperless(settings: Settings) -> None:
    client = PaperlessClient(settings)
    try:
        await client.list_documents({"page_size": 1})
    except httpx.HTTPError as exc:  # pragma: no cover - network boundary
        raise _wrap_http_error("paperless", exc) from exc


async def _ping_llm(settings: Settings) -> None:
    llm = TitleLLMClient(settings)
    try:
        # Use a tiny synthetic payload to validate connectivity and auth.
        await llm.propose_title("Onboarding connectivity test")
    except httpx.HTTPError as exc:  # pragma: no cover - network boundary
        raise _wrap_http_error("llm", exc) from exc


async def _validate_paperless(settings: Settings) -> None:
    await _ping_paperless(settings)


async def _validate_llm(settings: Settings) -> None:
    await _ping_llm(settings)