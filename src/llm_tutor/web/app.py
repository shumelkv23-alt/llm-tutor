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
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from llm_tutor.config import Settings
from llm_tutor.llm.client import LLMClient
from llm_tutor.web import accounts, security

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
    accounts_db = accounts.open_accounts(settings.accounts_db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            accounts_db.close()
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
    # Все эндпоинты — async def: соединения SQLite живут в потоке цикла
    # событий, а sync-эндпоинты FastAPI увёл бы в пул потоков.
    app.state.accounts = accounts_db
    app.state.login_limiter = security.LoginLimiter()

    # Порядок важен: добавленный позже middleware — внешний. Заголовки
    # безопасности должны лечь и на отказы api_guard.
    app.middleware("http")(security.api_guard)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(security.LoginRequired)
    async def login_required(request: Request, exc: security.LoginRequired) -> Response:
        return RedirectResponse(security.login_url(exc.next_path), status_code=303)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        """Ошибка формы в API — одно сообщение и поле, а не стек pydantic."""
        if not request.url.path.startswith("/api/"):
            return await request_validation_exception_handler(request, exc)
        errors = exc.errors()
        location = errors[0].get("loc", ()) if errors else ()
        field = str(location[-1]) if len(location) > 1 else None
        return JSONResponse(
            {"detail": {"field": field, "message": "Проверьте поля формы."}}, status_code=422
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Локальный импорт: модули маршрутов берут зависимости из app.state.
    from llm_tutor.web import api_auth, pages

    app.include_router(api_auth.router)
    app.include_router(pages.router)
    return app
