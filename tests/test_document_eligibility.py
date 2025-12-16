from paperless_ai_titles.core.config import Settings
from paperless_ai_titles.document_eligibility import (
    document_has_original_title_field,
    document_has_tag,
    document_passes_tag_filters,
)


def test_document_has_tag_matches_slug_or_name():
    tags = [
        {"slug": "finance", "name": "Finance"},
        {"slug": "home", "name": "House"},
    ]
    doc = {"tags": tags}
    assert document_has_tag(doc, "finance") is True
    assert document_has_tag(doc, "house") is True
    assert document_has_tag(doc, "missing") is False


def test_document_has_tag_handles_invalid_payload():
    doc = {"tags": ["bad", {"slug": None}, None]}
    assert document_has_tag(doc, "anything") is False


def test_document_has_original_title_field_handles_dict_payload():
    settings = Settings(paperless_original_title_field="original_title")
    doc = {
        "custom_fields": {
            "original_title": {"value": "Manual name"},
        }
    }
    assert document_has_original_title_field(doc, settings) is True


def test_document_has_original_title_field_handles_list_payload():
    settings = Settings(paperless_original_title_field="carry_over")
    doc = {
        "custom_fields": [
            {"slug": "other", "value": "foo"},
            {"slug": "carry_over", "field_value": "Stored"},
        ]
    }
    assert document_has_original_title_field(doc, settings) is True


def test_document_passes_tag_filters_respects_original_title_field():
    settings = Settings(paperless_original_title_field="original")
    doc = {
        "id": 99,
        "tags": [{"slug": "eligible"}],
        "custom_fields": {"original": {"value": "Existing"}},
    }
    eligible, reason = document_passes_tag_filters(doc, settings=settings)
    assert eligible is False
    assert "original" in reason


def test_document_passes_tag_filters_skip_and_require_rules():
    settings = Settings(paperless_skip_tag="skip-me", paperless_require_tag="needs-ai")
    eligible, reason = document_passes_tag_filters(
        {"id": 1, "tags": [{"slug": "skip-me"}]}, settings=settings
    )
    assert eligible is False
    assert "skip" in reason

    eligible, reason = document_passes_tag_filters(
        {"id": 2, "tags": []}, settings=settings
    )
    assert eligible is False
    assert "missing" in reason

    eligible, reason = document_passes_tag_filters(
        {"id": 3, "tags": [{"slug": "needs-ai"}]}, settings=settings
    )
    assert eligible is True
    assert "passes" in reason
