from types import SimpleNamespace

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


def test_queue_timeout_derivation_adds_margin():
    settings = SimpleNamespace(llm_request_timeout=120)
    assert _derived_timeout_seconds(settings) == 130