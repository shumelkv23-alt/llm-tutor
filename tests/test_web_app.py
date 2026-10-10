"""Каркас веб-приложения: сборка, статика, оболочка страниц (Срез 37)."""

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from llm_tutor.config import Settings
from llm_tutor.web.app import STATIC_DIR, TEMPLATES_DIR, create_app

PAGES = [
    ("/", "home"),
    ("/courses", "courses"),
    ("/lesson", "lesson"),
    ("/profile", "profile"),
]

# Внешние скрипты — только с CDN из спеки, и только с точной версией.
ALLOWED_HOSTS = {"cdn.jsdelivr.net", "cdnjs.cloudflare.com"}

_RESOURCE_RE = re.compile(r'(?:src|href)="([^"]+)"')


async def test_app_builds_without_telegram_token(web) -> None:
    """Вебу токен бота не нужен: фикстура его не задаёт."""
    response = await web.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_lifespan_closes_own_client(web_settings) -> None:
    """Клиент, созданный приложением, закрывается вместе с ним."""
    app = create_app(web_settings)
    client = app.state.client

    async with app.router.lifespan_context(app):
        pass

    assert client._http.is_closed


async def test_lifespan_leaves_injected_client_open(web_app, web_llm) -> None:
    """Внедрённый клиент закрывает тот, кто его создал."""
    async with web_app.router.lifespan_context(web_app):
        pass

    assert not web_llm.closed


async def test_api_docs_are_disabled(web) -> None:
    """Swagger грузит скрипты со стороннего CDN — нам он не нужен."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await web.get(path)).status_code == 404


async def test_static_files_are_served(web) -> None:
    response = await web.get("/static/css/app.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


@pytest.mark.parametrize(("path", "key"), PAGES)
async def test_page_renders_with_current_nav_item(web, path: str, key: str) -> None:
    response = await web.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    current = re.findall(r'<a [^>]*data-nav="(\w+)"[^>]*aria-current="page"', response.text)
    assert current == [key]


@pytest.mark.parametrize(("path", "key"), PAGES)
async def test_page_loads_only_allowed_external_resources(web, path: str, key: str) -> None:
    html = (await web.get(path)).text

    for url in _RESOURCE_RE.findall(html):
        parsed = urlparse(url)
        if parsed.scheme in ("", None) and not url.startswith("//"):
            continue
        assert parsed.scheme == "https", url
        assert parsed.hostname in ALLOWED_HOSTS, url
        assert re.search(r"@?\d+\.\d+\.\d+", url), f"версия не закреплена: {url}"


async def test_static_urls_carry_content_version(web) -> None:
    """Ссылки на статику несут хеш содержимого: правка CSS не застрянет в кэше."""
    html = (await web.get("/")).text

    links = re.findall(r'"(/static/[^"]+)"', html)
    assert links
    for link in links:
        assert re.search(r"\?v=[0-9a-f]{10}(#[\w-]+)?$", link), link


async def test_security_headers(web) -> None:
    response = await web.get("/")

    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    # Встроенные скрипты запрещены: весь JS — в файлах.
    assert "'unsafe-inline'" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


async def test_pages_have_no_inline_scripts_or_styles(web) -> None:
    """CSP запрещает встроенное — страница не должна на него полагаться."""
    for path, _ in PAGES:
        html = (await web.get(path)).text
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), path
        assert " style=" not in html, path
        assert "<style" not in html, path
        assert not re.search(r"\son[a-z]+=", html), path


async def test_unknown_page_is_404(web) -> None:
    assert (await web.get("/nope")).status_code == 404


def _sprite_ids() -> set[str]:
    sprite = (STATIC_DIR / "icons.svg").read_text(encoding="utf-8")
    return set(re.findall(r'<symbol id="([\w-]+)"', sprite))


def test_every_used_icon_exists_in_sprite() -> None:
    """Иконка, которой нет в спрайте, молча не рисуется — ловим это тестом."""
    used: set[str] = set()
    for path in [*TEMPLATES_DIR.rglob("*.html"), *STATIC_DIR.rglob("*.js")]:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"icon\(['\"]([\w-]+)['\"]\)", text))
        used |= set(re.findall(r"icons\.svg#([\w-]+)", text))
    used.discard("")

    assert used
    assert used <= _sprite_ids(), used - _sprite_ids()


def test_every_font_in_css_exists() -> None:
    for css in STATIC_DIR.rglob("*.css"):
        for url in re.findall(r"url\(['\"]?([^'\")]+)['\"]?\)", css.read_text(encoding="utf-8")):
            if url.startswith("data:"):
                continue
            target = (css.parent / url).resolve() if not url.startswith("/") else None
            if url.startswith("/static/"):
                target = STATIC_DIR / url.removeprefix("/static/")
            assert target is not None and Path(target).is_file(), f"{css.name}: {url}"


def test_settings_web_defaults() -> None:
    settings = Settings(_env_file=None, openrouter_api_key="k")

    assert settings.telegram_bot_token is None
    assert settings.web_host == "127.0.0.1"
    assert settings.web_port == 8000
    assert settings.accounts_db_path == "data/accounts.sqlite3"
    assert settings.users_dir == "data/users"
    assert settings.materials_db_path == "data/materials.sqlite3"
    assert settings.registration_open is True
    assert settings.cookie_secure is False
