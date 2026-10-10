"""Общие фикстуры pytest для LLM-тьютора."""

import httpx
import pytest

from llm_tutor.config import Settings
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.web.app import create_app
from web_fakes import SilentLLM


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


@pytest.fixture
def web_settings(tmp_path):
    """Настройки веба: все файлы — во временном каталоге, Telegram не нужен."""
    return Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        accounts_db_path=str(tmp_path / "accounts.sqlite3"),
        users_dir=str(tmp_path / "users"),
        materials_db_path=str(tmp_path / "materials.sqlite3"),
    )


@pytest.fixture
def web_llm():
    """LLM веб-приложения; тест подменяет его, если модель нужна."""
    return SilentLLM()


@pytest.fixture
def web_app(web_settings, web_llm):
    app = create_app(web_settings, client=web_llm)
    yield app
    app.state.accounts.close()


@pytest.fixture
async def web(web_app):
    """HTTP-клиент к приложению в памяти процесса (без сети и uvicorn)."""
    transport = httpx.ASGITransport(app=web_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def student(web):
    """Клиент вошедшего ученика (регистрация через API)."""
    response = await web.post(
        "/api/auth/register",
        json={"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"},
    )
    assert response.status_code == 201
    return web
