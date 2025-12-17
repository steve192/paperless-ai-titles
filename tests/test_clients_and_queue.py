from types import SimpleNamespace

import pytest

from paperless_ai_titles.clients.llm_client import TitleLLMClient
from paperless_ai_titles.core.config import Settings
from paperless_ai_titles.queue_factory import _derived_timeout_seconds


def test_parse_json_content_handles_code_fences():
    payload = """```json\n{\"title\": \"Doc\", \"confidence\": 1.2}\n```"""
    parsed = TitleLLMClient._parse_json_content(payload)
    assert parsed["title"] == "Doc"
    assert parsed["confidence"] == 1.2


def test_normalize_confidence_clamps_values():
    assert TitleLLMClient._normalize_confidence(-1) == 0.0
    assert TitleLLMClient._normalize_confidence(5) == 1.0
    assert TitleLLMClient._normalize_confidence("bad") is None


def test_extract_content_and_truncate_text():
    response = {"choices": [{"message": {"content": "result"}}]}
    assert TitleLLMClient._extract_content(response) == "result"

    client = TitleLLMClient(settings=Settings())
    client.settings.llm_prompt_char_limit = 5
    assert client._truncate_text("abcdefg") == "abcde"


@pytest.mark.asyncio
async def test_propose_title_retries_once_on_invalid_json_then_succeeds():
    settings = Settings()
    client = TitleLLMClient(settings=settings)

    calls: list[dict] = []

    async def fake_post(payload: dict):
        calls.append(payload)
        # First response: broken JSON
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"title":"Rechnung",confidence":0.9}',
                        }
                    }
                ]
            }
        # Second response: valid JSON
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"title": "Valid title", "confidence": 0.8}',
                    }
                }
            ]
        }

    client._post = fake_post  # type: ignore[assignment]

    suggestion = await client.propose_title("Some document text")

    assert suggestion.title == "Valid title"
    assert suggestion.confidence == 0.8
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_propose_title_raises_after_two_invalid_json_attempts():
    settings = Settings()
    client = TitleLLMClient(settings=settings)

    calls: list[dict] = []

    async def fake_post_invalid(payload: dict):
        calls.append(payload)
        # Always return invalid JSON content
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"title":"Rechnung",confidence":0.9}',
                    }
                }
            ]
        }

    client._post = fake_post_invalid  # type: ignore[assignment]

    with pytest.raises(ValueError) as exc:
        await client.propose_title("Some document text")

    assert "non-JSON" in str(exc.value)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_evaluate_title_uses_json_and_does_not_fall_back_to_raw_text():
    settings = Settings()
    client = TitleLLMClient(settings=settings)

    calls: list[dict] = []

    async def fake_post(payload: dict):
        calls.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"decision": "GOOD", "acceptable": true, "confidence": 0.95}',
                    }
                }
            ]
        }

    client._post = fake_post  # type: ignore[assignment]

    evaluation = await client.evaluate_title("Title", "Some text")

    assert evaluation.acceptable is True
    assert evaluation.confidence == 0.95
    assert len(calls) == 1


def test_queue_timeout_derivation_adds_margin():
    settings = SimpleNamespace(llm_request_timeout=120)
    assert _derived_timeout_seconds(settings) == 130