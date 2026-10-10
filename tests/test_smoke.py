"""Дымовой тест зависимостей: все ключевые библиотеки проекта установлены."""

from importlib import metadata

import bs4  # noqa: F401  — импорт сам по себе и есть сигнал, что модуль ставится
import fastapi  # noqa: F401
import httpx  # noqa: F401
import jinja2  # noqa: F401
import markdown_it  # noqa: F401
import markdownify  # noqa: F401
import networkx  # noqa: F401
import pydantic  # noqa: F401
import pydantic_settings  # noqa: F401
import pytest_asyncio  # noqa: F401
import respx  # noqa: F401
import uvicorn  # noqa: F401

# Имя дистрибутива для metadata.version() — проверяет реально установленную версию.
DISTRIBUTIONS = [
    "fastapi",
    "uvicorn",
    "jinja2",
    "markdown-it-py",
    "httpx",
    "pydantic",
    "pydantic-settings",
    "networkx",
    "beautifulsoup4",
    "markdownify",
    "pytest-asyncio",
    "respx",
]


def test_dependencies_installed() -> None:
    """Все ключевые зависимости известны пакетному менеджеру и имеют версию."""
    for dist_name in DISTRIBUTIONS:
        assert metadata.version(dist_name), f"дистрибутив {dist_name} не найден"
