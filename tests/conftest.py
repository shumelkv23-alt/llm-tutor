"""Общие фикстуры pytest для LLM-тьютора."""

import pytest

from llm_tutor.config import Settings
from llm_tutor.db.connection import get_conn, migrate


@pytest.fixture
def conn():
    """Соединение с мигрированной БД в памяти (`:memory:`)."""
    connection = get_conn(":memory:")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def settings():
    """Настройки с фиктивными секретами и без чтения `.env`."""
    return Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        telegram_bot_token="test-token",
    )
