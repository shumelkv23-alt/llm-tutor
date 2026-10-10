"""Регрессии по ревью среза 37."""

import os
import time

import httpx
import pytest
from fastapi import APIRouter

from llm_tutor.config import Settings
from llm_tutor.web import app as app_module
from llm_tutor.web.app import STATIC_DIR, create_app, static_url

HEADERS = ("content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy")


@pytest.fixture
async def raw_web(web_settings, web_llm):
    """Клиент, который видит ответ 500, а не исключение приложения."""
    application = create_app(web_settings, client=web_llm)
    boom = APIRouter()

    @boom.get("/boom")
    async def explode() -> None:
        raise RuntimeError("бум")

    application.include_router(boom)
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    application.state.accounts.close()


@pytest.mark.parametrize("path", ["/", "/static/css/app.css", "/nope", "/static/nope.css", "/boom"])
async def test_security_headers_on_every_response(raw_web, path: str) -> None:
    response = await raw_web.get(path)

    for name in HEADERS:
        assert name in response.headers, (path, name, response.status_code)
    assert response.headers["x-frame-options"] == "DENY"


async def test_unhandled_error_is_plain_500(raw_web) -> None:
    response = await raw_web.get("/boom")

    assert response.status_code == 500
    assert "бум" not in response.text


def test_static_version_follows_file_changes(tmp_path, monkeypatch) -> None:
    static = tmp_path / "static"
    static.mkdir()
    css = static / "x.css"
    css.write_text("a{}", encoding="utf-8")
    monkeypatch.setattr(app_module, "STATIC_DIR", static)
    first = static_url("x.css")

    css.write_text("a{color:red}", encoding="utf-8")
    os.utime(css, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))

    assert static_url("x.css") != first


def test_missing_or_foreign_static_file_has_no_version() -> None:
    assert static_url("nope.css") == "/static/nope.css"
    assert "?v=" not in static_url("../app.py")
    assert (STATIC_DIR / "css" / "app.css").is_file()


async def test_versioned_static_is_cached_long(web) -> None:
    html = (await web.get("/")).text
    link = next(part for part in html.split('"') if part.startswith("/static/css/app.css?v="))

    response = await web.get(link)

    assert "immutable" in response.headers["cache-control"]


