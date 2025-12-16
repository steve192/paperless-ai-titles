from paperless_ai_titles.services.settings import ONBOARDING_FLAG, SettingsService


def test_effective_settings_overrides_saved_value():
    service = SettingsService()
    service.save("llm_request_timeout", 999)
    effective = service.effective_settings()
    assert effective.llm_request_timeout == 999


def test_effective_settings_respects_booleans():
    service = SettingsService()
    service.save("auto_apply_titles", False)
    effective = service.effective_settings()
    assert effective.auto_apply_titles is False


def test_save_rejects_unknown_keys():
    service = SettingsService()
    try:
        service.save("not-real", "value")
    except ValueError as exc:
        assert "not configurable" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected ValueError for unknown key")


def test_overrides_skips_onboarding_flag():
    service = SettingsService()
    service.save("llm_model_name", "demo-model")
    service.save(ONBOARDING_FLAG, "true")
    overrides = service.overrides()
    assert "llm_model_name" in overrides
    assert ONBOARDING_FLAG not in overrides


def test_bootstrap_defaults_merges_overrides():
    service = SettingsService()
    defaults = service.bootstrap_defaults()
    assert "paperless_base_url" in defaults
    service.save("paperless_require_tag", "needs-ai")
    defaults = service.bootstrap_defaults()
    assert defaults["paperless_require_tag"] == "needs-ai"


def test_onboarding_flow_and_missing_keys():
    service = SettingsService()
    assert service.needs_onboarding() is True  # onboarding flag absent
    service.mark_onboarding_complete()
    assert service.needs_onboarding() is False
    service.save("paperless_api_token", "")
    missing = service.missing_keys()
    assert "paperless_api_token" in missing
    assert service.needs_onboarding() is True


def test_delete_removes_entry():
    service = SettingsService()
    service.save("llm_model_name", "demo")
    assert service.list_entries()
    service.delete("llm_model_name")
    assert all(entry.key != "llm_model_name" for entry in service.list_entries())
