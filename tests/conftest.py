"""Общие фикстуры pytest для LLM-тьютора."""

import pytest

from llm_tutor.db.connection import get_conn, migrate


@pytest.fixture
def conn():
    """Соединение с мигрированной БД в памяти (`:memory:`)."""
    connection = get_conn(":memory:")
    migrate(connection)
    yield connection
    connection.close()
