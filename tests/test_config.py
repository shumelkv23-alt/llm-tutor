"""Тесты конфигурации (pydantic-settings)."""

import pytest
from pydantic import ValidationError

from llm_tutor.config import Settings, get_settings


def _settings(**overrides) -> Settings:
    """Настройки с заполненными секретами и отключённым чтением .env."""
    defaults = {
        "openrouter_api_key": "sk-test",
        "_env_file": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_defaults_load_with_explicit_keys() -> None:
    """С явными секретами настройки читаются, дефолты осмысленны."""
    settings = _settings()

    assert settings.openrouter_api_key.get_secret_value() == "sk-test"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.tutor_model == "anthropic/claude-sonnet-4.5"
    assert settings.llm_temperature == 0.4
    assert settings.llm_max_tokens == 2048
    assert settings.context_dialog_tail == 8


def test_empty_openrouter_key_raises_clear_error() -> None:
    """Пустой OPENROUTER_API_KEY падает с ясной ошибкой."""
    with pytest.raises(ValidationError) as exc_info:
        _settings(openrouter_api_key="")

    assert "OPENROUTER_API_KEY" in str(exc_info.value)


def test_retired_settings_from_old_env_are_ignored(tmp_path) -> None:
    """Старый .env (с токеном бота и диагностикой) не мешает запуску веба (Срез 49)."""
    env = tmp_path / ".env"
    env.write_text(
        "OPENROUTER_API_KEY=sk-test\nTELEGRAM_BOT_TOKEN=123:abc\nDIAGNOSTIC_FIRST_PASS=5\n"
        "ITEM_REPEAT_COOLDOWN_DAYS=1.0\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env)

    assert settings.openrouter_api_key.get_secret_value() == "sk-test"
    assert not hasattr(settings, "telegram_bot_token")


def test_typo_in_env_still_fails(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=sk-test\nTUTOR_MDOEL=x\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings(_env_file=env)


def test_whitespace_only_key_raises() -> None:
    """Ключ из одних пробелов тоже считается пустым."""
    with pytest.raises(ValidationError):
        _settings(openrouter_api_key="   ")


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings() возвращает один и тот же объект (lru_cache)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    try:
        first = get_settings()
        second = get_settings()
        assert first is second
    finally:
        get_settings.cache_clear()
