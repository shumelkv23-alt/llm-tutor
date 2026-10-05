"""Дымовой тест зависимостей: все ключевые библиотеки проекта установлены."""

from importlib import metadata

import aiogram
import bs4
import httpx
import markdownify
import networkx
import pydantic
import pydantic_settings
import pytest_asyncio
import respx

# Имя модуля (для импорта) -> имя дистрибутива (для metadata.version)
DISTRIBUTIONS = {
    "aiogram": "aiogram",
    "httpx": "httpx",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "networkx": "networkx",
    "bs4": "beautifulsoup4",
    "markdownify": "markdownify",
    "pytest_asyncio": "pytest-asyncio",
    "respx": "respx",
}


def test_dependencies_installed() -> None:
    """Все ключевые зависимости импортируются и имеют известную версию."""
    assert aiogram
    assert bs4
    assert httpx
    assert markdownify
    assert networkx
    assert pydantic
    assert pydantic_settings
    assert pytest_asyncio
    assert respx

    for dist_name in DISTRIBUTIONS.values():
        assert metadata.version(dist_name), f"дистрибутив {dist_name} не найден"
