import asyncio

import httpx
import pytest

from paperless_ai_titles.core.config import Settings
from paperless_ai_titles.services import onboarding as onboarding_module
from paperless_ai_titles.services.onboarding import (
    OnboardingConnectionError,
    OnboardingService,
    _wrap_http_error,
    _ping_paperless,
    _ping_llm,
)


def test_wrap_http_error_extracts_url_and_status_code():
    request = httpx.Request("GET", "https://example.com/api/test")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    wrapped = _wrap_http_error("paperless", exc)

    assert isinstance(wrapped, OnboardingConnectionError)
    assert wrapped.service == "paperless"
    assert wrapped.status_code == 401
    assert wrapped.url == "https://example.com/api/test"
    assert "401" in wrapped.message


async def test_validate_connections_calls_helpers(monkeypatch):
    called = {"paperless": False, "llm": False}

    async def fake_validate_paperless(settings: Settings):  # noqa: ARG001
        called["paperless"] = True
        # introduce an actual await to satisfy async linters
        await asyncio.sleep(0)

    async def fake_validate_llm(settings: Settings):  # noqa: ARG001
        called["llm"] = True
        await asyncio.sleep(0)

    monkeypatch.setattr(onboarding_module, "_validate_paperless", fake_validate_paperless)
    monkeypatch.setattr(onboarding_module, "_validate_llm", fake_validate_llm)

    service = OnboardingService()
    await service.validate_connections({})

    assert called["paperless"] is True
    assert called["llm"] is True


async def test_validate_connections_propagates_onboarding_error(monkeypatch):
    async def fake_validate_paperless(settings: Settings):  # noqa: ARG001
        raise OnboardingConnectionError(
            "paperless",
            url="https://example.com/api/documents/",
            status_code=503,
            message="Service unavailable",
        )

    # Only patch paperless; LLM validation should not be reached.
    monkeypatch.setattr(onboarding_module, "_validate_paperless", fake_validate_paperless)

    service = OnboardingService()

    try:
        await service.validate_connections({})
    except OnboardingConnectionError as exc:
        assert exc.service == "paperless"
        assert exc.status_code == 503
        assert "Service unavailable" in exc.message
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected OnboardingConnectionError to be raised")


async def test_ping_paperless_wraps_http_errors(monkeypatch):
    """_ping_paperless should translate httpx errors into OnboardingConnectionError."""

    class DummyClient:
        def __init__(self, settings):  # noqa: ARG002
            self._settings = settings

        async def list_documents(self, params):  # noqa: ARG002
            await asyncio.sleep(0)
            request = httpx.Request("GET", "https://paperless.example.com/api/documents/")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("503", request=request, response=response)

    monkeypatch.setattr(onboarding_module, "PaperlessClient", DummyClient)

    with pytest.raises(OnboardingConnectionError) as ctx:
        await _ping_paperless(Settings())

    err = ctx.value
    assert err.service == "paperless"
    assert err.status_code == 503
    assert "paperless.example.com" in (err.url or "")


async def test_ping_llm_wraps_http_errors(monkeypatch):
    """_ping_llm should translate httpx errors into OnboardingConnectionError."""

    class DummyLLM:
        def __init__(self, settings):  # noqa: ARG002
            self._settings = settings

        async def propose_title(self, text):  # noqa: ARG002
            await asyncio.sleep(0)
            request = httpx.Request("POST", "http://llm.local/v1/chat/completions")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(onboarding_module, "TitleLLMClient", DummyLLM)

    with pytest.raises(OnboardingConnectionError) as ctx:
        await _ping_llm(Settings())

    err = ctx.value
    assert err.service == "llm"
    assert err.status_code == 401
    assert "llm.local" in (err.url or "")
