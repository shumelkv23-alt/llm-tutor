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
    assert settings.llm_max_tokens == 3072
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


# --- Почта: восстановление пароля (Срез 71) ---


def test_smtp_is_optional_and_off_by_default() -> None:
    """У старых .env почтовых переменных нет — приложение обязано стартовать."""
    settings = _settings()

    assert settings.smtp_host == "smtp.gmail.com"
    assert settings.smtp_port == 587
    assert settings.smtp_starttls is True
    assert settings.smtp_configured is False
    assert settings.mail_from_name == "llm·tutor"
    assert settings.public_base_url == "http://127.0.0.1:8000"
    assert settings.password_reset_ttl_minutes == 60
    assert settings.password_reset_limit == 3


@pytest.mark.parametrize(
    ("user", "password", "expected"),
    [
        ("tutor@example.com", "abcd efgh ijkl mnop", True),
        ("tutor@example.com", None, False),
        (None, "abcd efgh ijkl mnop", False),
        ("tutor@example.com", "", False),
        ("   ", "abcd efgh ijkl mnop", False),
        ("tutor@example.com", "   ", False),
    ],
)
def test_smtp_configured_needs_address_and_password(user, password, expected) -> None:
    """Отправка возможна, только когда есть и адрес, и непустой пароль."""
    settings = _settings(smtp_user=user, smtp_password=password)

    assert settings.smtp_configured is expected


def test_password_reset_ttl_is_read_in_minutes(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OPENROUTER_API_KEY=sk-test\nPASSWORD_RESET_TTL_MINUTES=15\n"
        "SMTP_USER=tutor@example.com\nSMTP_PASSWORD=abcd efgh ijkl mnop\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env)

    assert settings.password_reset_ttl_minutes == 15
    assert settings.smtp_configured is True


def test_smtp_password_is_not_shown_in_repr() -> None:
    """Секрет не светится ни в логе, ни в тексте ошибки настроек."""
    settings = _settings(smtp_user="tutor@example.com", smtp_password="пароль-приложения")

    assert "пароль-приложения" not in repr(settings)


@pytest.mark.parametrize(
    "overrides", [{"password_reset_ttl_minutes": 0}, {"password_reset_limit": 0}]
)
def test_password_reset_bounds_are_validated(overrides) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)
