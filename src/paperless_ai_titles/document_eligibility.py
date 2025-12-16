from __future__ import annotations

"""Helpers that enforce the tag-based eligibility rules for documents."""

import logging
from typing import Any, Iterable, Tuple

from .core.config import Settings
from .services.settings import SettingsService

logger = logging.getLogger(__name__)

def document_has_tag(document: dict[str, Any], slug: str | None) -> bool:
    needle = _normalize_tag(slug)
    if not needle:
        return False
    tags = document.get("tags") or []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        values = (
            _normalize_tag(tag.get("slug")),
            _normalize_tag(tag.get("name")),
        )
        if needle in values:
            return True
    return False


def document_passes_tag_filters(
    document: dict[str, Any], settings: Settings | None = None
) -> Tuple[bool, str]:
    settings = settings or SettingsService().effective_settings()
    document_id = document.get("id")
    logger.debug(
        "Evaluating eligibility for document %s (skip=%s require=%s)",
        document_id,
        settings.paperless_skip_tag,
        settings.paperless_require_tag,
    )

    if settings.paperless_skip_tag and document_has_tag(document, settings.paperless_skip_tag):
        logger.debug("Document %s rejected: skip tag present", document_id)
        return False, "skip tag present"
    if document_has_original_title_field(document, settings):
        logger.debug("Document %s rejected: original title already stored", document_id)
        return False, "original title already stored"
    if settings.paperless_require_tag and not document_has_tag(
        document, settings.paperless_require_tag
    ):
        logger.debug("Document %s rejected: missing required tag", document_id)
        return False, "missing required tag"
    logger.debug("Document %s passes tag filters", document_id)
    return True, "passes tag filters"


def document_has_original_title_field(document: dict[str, Any], settings: Settings) -> bool:
    slug = settings.paperless_original_title_field
    if not slug:
        return False
    slug = slug.strip()
    if not slug:
        return False
    value = _extract_custom_field_value(document.get("custom_fields"), slug)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _extract_custom_field_value(custom_fields: Any, slug: str) -> Any:
    if not custom_fields:
        return None
    slug = slug.lower()
    if isinstance(custom_fields, dict):
        for key, value in custom_fields.items():
            key_slug = str(key).lower()
            if key_slug == slug:
                if isinstance(value, dict):
                    return value.get("value") or value.get("data") or value.get("field_value")
                return value
    if isinstance(custom_fields, list):
        for entry in custom_fields:
            if not isinstance(entry, dict):
                continue
            entry_slug = (
                entry.get("slug")
                or entry.get("key")
                or ((entry.get("field") or {}).get("slug"))
                or ((entry.get("field_definition") or {}).get("slug"))
            )
            if not entry_slug:
                continue
            if entry_slug.lower() != slug:
                continue
            if "value" in entry:
                return entry["value"]
            if "field_value" in entry:
                return entry["field_value"]
            if "data" in entry:
                return entry["data"]
    return None


def _normalize_tag(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized or None
    return str(value).strip().lower() or None
