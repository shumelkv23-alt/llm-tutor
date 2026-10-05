"""Тесты конфигурации (pydantic-settings)."""

import pytest
from pydantic import ValidationError

from llm_tutor.config import Settings, get_settings


def _settings(**overrides) -> Settings:
    """Настройки с заполненными секретами и отключённым чтением .env."""
    defaults = {
        "openrouter_api_key": "sk-test",
        "telegram_bot_token": "bot-test",
        "_env_file": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_defaults_load_with_explicit_keys() -> None:
    """С явными секретами настройки читаются, дефолты осмысленны."""
    settings = _settings()

    assert settings.openrouter_api_key.get_secret_value() == "sk-test"
    assert settings.telegram_bot_token.get_secret_value() == "bot-test"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.tutor_model == "anthropic/claude-sonnet-4.5"
    assert settings.db_path == "data/llm_tutor.sqlite3"
    assert settings.llm_temperature == 0.4
    assert settings.context_dialog_tail == 8


def test_empty_openrouter_key_raises_clear_error() -> None:
    """Пустой OPENROUTER_API_KEY падает с ясной ошибкой."""
    with pytest.raises(ValidationError) as exc_info:
        _settings(openrouter_api_key="")

    assert "OPENROUTER_API_KEY" in str(exc_info.value)


def test_empty_telegram_token_raises_clear_error() -> None:
    """Пустой TELEGRAM_BOT_TOKEN падает с ясной ошибкой."""
    with pytest.raises(ValidationError) as exc_info:
        _settings(telegram_bot_token="")

    assert "TELEGRAM_BOT_TOKEN" in str(exc_info.value)


def test_whitespace_only_key_raises() -> None:
    """Ключ из одних пробелов тоже считается пустым."""
    with pytest.raises(ValidationError):
        _settings(openrouter_api_key="   ")


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings() возвращает один и тот же объект (lru_cache)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-test")
    get_settings.cache_clear()
    try:
        first = get_settings()
        second = get_settings()
        assert first is second
    finally:
        get_settings.cache_clear()
