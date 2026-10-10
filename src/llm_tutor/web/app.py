"""Фабрика веб-приложения: маршруты, статика, шаблоны, заголовки безопасности.

Зависимости (настройки, LLM-клиент) внедряются через ``create_app``, как в
``bot.handlers.make_router``: тесты собирают приложение без сети и uvicorn.
"""

import hashlib
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from llm_tutor.config import Settings
from llm_tutor.llm.client import LLMClient

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"

# Встроенные скрипты и стили запрещены: весь JS и CSS — в файлах. Внешние
# источники (Pyodide, CodeMirror) добавляются сюда вместе с кодом, которому
# они нужны.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


@lru_cache(maxsize=256)
def _content_version(relative: str) -> str:
    """Короткий хеш содержимого файла статики (для сброса кэша браузера)."""
    return hashlib.sha256((STATIC_DIR / relative).read_bytes()).hexdigest()[:10]


def static_url(relative: str) -> str:
    """URL статики с версией по содержимому: ``/static/css/app.css?v=…``."""
    return f"/static/{relative}?v={_content_version(relative)}"


def icon(name: str) -> Markup:
    """Иконка из SVG-спрайта (готовый HTML для шаблонов)."""
    return Markup(
        '<svg class="icon" aria-hidden="true"><use href="{}#{}"></use></svg>'
    ).format(static_url("icons.svg"), escape(name))


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.globals.update(static_url=static_url, icon=icon)
    return templates


def _llm_client(settings: Settings) -> LLMClient:
    return LLMClient(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        default_model=settings.tutor_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=settings.llm_max_retries,
    )


def create_app(settings: Settings, client: LLMClient | None = None) -> FastAPI:
    """Собирает приложение.

    ``client`` — LLM-клиент; если не передан, приложение создаёт свой и
    закрывает его при остановке. Внедрённый клиент закрывает тот, кто его
    создал.
    """
    owns_client = client is None
    llm = _llm_client(settings) if client is None else client

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if owns_client:
                await llm.aclose()

    # Swagger и ReDoc грузят скрипты со стороннего CDN — API внутреннее.
    app = FastAPI(
        title="llm·tutor",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.client = llm
    app.state.templates = make_templates()

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Локальный импорт: pages берёт шаблоны из app.state, а не из модуля.
    from llm_tutor.web import pages

    app.include_router(pages.router)
    return app
