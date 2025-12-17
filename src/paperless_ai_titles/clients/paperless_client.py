from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TypedDict

import httpx

from ..core.config import Settings
from ..services.settings import SettingsService

DEFAULT_TIMEOUT = httpx.Timeout(30.0)
logger = logging.getLogger(__name__)


class PaperlessDocument(TypedDict, total=False):
    id: int
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    title: str | None
    content: str | None
    tags: list[Any]
    created: str | None
    created_date: str | None
    modified: str | None
    added: str | None
    custom_fields: list[Any] | dict[str, Any]


class PaperlessClient:
    """Thin async wrapper around the Paperless-NGX REST API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or SettingsService().effective_settings()
        self.base_url = str(self.settings.paperless_base_url).rstrip("/")
        self.headers = {
            "Authorization": f"Token {self.settings.paperless_api_token}",
            "Content-Type": "application/json",
        }
        self._custom_field_cache: dict[str, dict[str, Any]] = {}
        self._custom_fields_loaded = False

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
        payload_keys = None
        if "json" in kwargs and isinstance(kwargs["json"], dict):
            payload_keys = list(kwargs["json"].keys())
        logger.debug(
            "Paperless request %s %s params=%s payload_keys=%s",
            method,
            path,
            kwargs.get("params"),
            payload_keys,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            logger.debug(
                "Paperless response %s %s status=%s",
                method,
                path,
                response.status_code,
            )
            return response

    async def fetch_document(self, document_id: int, *, expand: Optional[str] = None) -> PaperlessDocument:
        params = {"expand": expand} if expand else None
        response = await self._request("GET", f"/api/documents/{document_id}/", params=params)
        return response.json()

    async def list_documents(self, params: Optional[Dict[str, Any]] = None) -> dict[str, Any]:
        response = await self._request("GET", "/api/documents/", params=params)
        return response.json()

    async def list_tags(self, page_size: int = 100) -> list[dict[str, Any]]:
        return await self._list_collection("/api/tags/", page_size=page_size)

    async def create_tag(self, name: str, color: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name.strip(), "match": "", "matching_algorithm": 0}  
        if color:
            payload["color"] = color
        response = await self._request("POST", "/api/tags/", json=payload)
        return response.json()

    async def list_custom_fields(self, page_size: int = 100) -> list[dict[str, Any]]:
        return await self._list_collection("/api/custom_fields/", page_size=page_size)

    async def create_custom_field(self, name: str, data_type: str = "string") -> dict[str, Any]:
        payload = {"name": name.strip(), "data_type": data_type}
        response = await self._request("POST", "/api/custom_fields/", json=payload)
        return response.json()

    async def update_document(self, document_id: int, payload: Dict[str, Any]) -> dict[str, Any]:
        response = await self._request("PATCH", f"/api/documents/{document_id}/", json=payload)
        return response.json()

    async def update_title(self, document_id: int, title: str) -> dict[str, Any]:
        return await self.update_document(document_id, {"title": title})

    async def add_tag(self, document_id: int, tag: str) -> None:
        if not tag:
            return
        payload = {"name": tag}
        await self._request("POST", f"/api/documents/{document_id}/add_tag/", json=payload)

    async def remove_tag(self, document_id: int, tag: str) -> None:
        if not tag:
            return
        payload = {"name": tag}
        await self._request("POST", f"/api/documents/{document_id}/remove_tag/", json=payload)

    async def set_custom_field(self, document_id: int, field_slug: str, value: str) -> dict[str, Any]:
        field = await self._resolve_custom_field(field_slug)
        if not field or field.get("id") is None:
            raise ValueError(f"Custom field '{field_slug}' is not defined in Paperless")
        logger.debug(
            "Setting custom field %s (id=%s) for document %s",
            field_slug,
            field.get("id"),
            document_id,
        )
        payload = {
            "custom_fields": [
                {
                    "field": field["id"],
                    "value": value,
                }
            ]
        }
        return await self.update_document(document_id, payload)

    async def _resolve_custom_field(self, identifier: str) -> Optional[dict[str, Any]]:
        key = (identifier or "").strip().lower()
        if not key:
            return None
        if key in self._custom_field_cache:
            return self._custom_field_cache[key]
        if not self._custom_fields_loaded:
            await self._refresh_custom_field_cache()
        field = self._custom_field_cache.get(key)
        if not field:
            logger.debug("Custom field '%s' not found after cache refresh", identifier)
        return field

    async def _refresh_custom_field_cache(self) -> None:
        fields = await self.list_custom_fields()
        cache: dict[str, dict[str, Any]] = {}
        for field in fields:
            for candidate in self._field_cache_keys(field):
                cache[candidate] = field
        self._custom_field_cache = cache
        self._custom_fields_loaded = True
        logger.debug("Cached %s custom field entries", len(cache))

    def _field_cache_keys(self, field: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        for raw in (
            field.get("slug"),
            field.get("name"),
            str(field.get("id")) if field.get("id") is not None else None,
        ):
            if not raw:
                continue
            keys.append(str(raw).strip().lower())
        return keys

    async def _list_collection(self, path: str, *, page_size: int = 100) -> list[dict[str, Any]]:
        page = 1
        results: list[dict[str, Any]] = []
        while True:
            params = {"page": page, "page_size": page_size}
            response = await self._request("GET", path, params=params)
            data = response.json()
            results.extend(data.get("results", []))
            if not data.get("next"):
                break
            page += 1
        return results
