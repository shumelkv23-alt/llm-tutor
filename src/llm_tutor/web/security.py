"""Сессии, защита от CSRF, лимит попыток входа и безопасные редиректы.

CSRF закрывается без отдельного токена (спека веба §4): изменяющие запросы к
``/api/*`` — только JSON (форму с чужого сайта так не отправить без CORS), а
присланные браузером ``Origin`` и ``Sec-Fetch-Site`` должны указывать на тот же
сайт. Вместе с cookie ``SameSite=Lax`` этого достаточно.
"""

import sqlite3
import time
from collections import deque
from collections.abc import Awaitable, Callable
from urllib.parse import quote, urlparse

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from llm_tutor.config import Settings
from llm_tutor.web import accounts
from llm_tutor.web.accounts import User

SESSION_COOKIE = "lt_session"

LOGIN_REQUIRED = "Войдите, чтобы продолжить."
JSON_REQUIRED = "Запрос должен быть в формате JSON."
FOREIGN_ORIGIN = "Запрос пришёл с другого сайта и отклонён."
TOO_MANY_ATTEMPTS = "Слишком много неудачных попыток. Подождите 15 минут и попробуйте снова."

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LoginRequired(Exception):
    """Страница только для вошедших: обработчик уводит на ``/login?next=…``."""

    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


def safe_next(value: str | None) -> str:
    """Куда вернуть после входа: только свой путь, иначе ``/``.

    Отсекает открытый редирект: ``//evil``, ``/\\evil``, схемы, а также
    управляющие символы и пробелы — браузер выбрасывает табы и переводы строк
    из URL, и ``/\\t/evil`` превратился бы в ``//evil``.
    """
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return "/"
    if urlparse(value).netloc:
        return "/"
    return value


def login_url(next_path: str) -> str:
    return f"/login?next={quote(safe_next(next_path), safe='')}"


# --- Сессия ---


def accounts_db(request: Request) -> sqlite3.Connection:
    return request.app.state.accounts


def session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def optional_user(request: Request) -> User | None:
    """Вошедший ученик или ``None`` (результат кэшируется на запрос)."""
    if not hasattr(request.state, "user"):
        token = session_token(request)
        user, extended = (
            accounts.resolve_session_info(accounts_db(request), token) if token else (None, False)
        )
        request.state.user = user
        # Срок на сервере продлён — продлим и cookie (см. session_refresh).
        request.state.session_extended = extended
    return request.state.user


async def require_user(request: Request) -> User:
    """Зависимость API: без сессии — 401."""
    user = optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"message": LOGIN_REQUIRED})
    return user


async def page_user(request: Request) -> User:
    """Зависимость страницы: без сессии — редирект на вход с возвратом."""
    user = optional_user(request)
    if user is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise LoginRequired(target)
    return user


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(accounts.SESSION_TTL),
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=settings.cookie_secure
    )


# --- CSRF ---


def _foreign_request(request: Request) -> bool:
    """Пришёл ли изменяющий запрос не с нашего сайта (по заголовкам браузера)."""
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        return True
    origin = request.headers.get("origin")
    if origin is not None:
        # Сравниваем хост, а не схему: за TLS-прокси приложение видит http.
        if urlparse(origin).netloc != request.headers.get("host"):
            return True
    return False


async def api_guard(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Middleware: изменяющие запросы к API — только JSON и только со своего сайта.

    Проверка в middleware, а не в каждом маршруте: новый эндпоинт не сможет
    её забыть.
    """
    if request.url.path.startswith("/api/") and request.method in _UNSAFE_METHODS:
        if _foreign_request(request):
            return JSONResponse({"detail": {"message": FOREIGN_ORIGIN}}, status_code=403)
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return JSONResponse({"detail": {"message": JSON_REQUIRED}}, status_code=415)
    return await call_next(request)


# --- Лимит попыток входа ---


class LoginLimiter:
    """Не больше ``limit`` неудачных входов на email за ``window`` секунд.

    Счётчик в памяти процесса: рестарт его сбрасывает, для локального
    приложения этого достаточно (спека веба §4).
    """

    def __init__(
        self,
        *,
        limit: int = 5,
        window: float = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._failures: dict[str, deque[float]] = {}

    @staticmethod
    def _key(email: str) -> str:
        return email.strip().lower()

    def _recent(self, email: str) -> deque[float]:
        key = self._key(email)
        now = self.clock()
        failures = self._failures.get(key)
        if failures is None:
            return deque()
        while failures and now - failures[0] >= self.window:
            failures.popleft()
        if not failures:
            del self._failures[key]
        return failures

    def blocked(self, email: str) -> bool:
        return len(self._recent(email)) >= self.limit

    # Сколько email держать, прежде чем вычистить всех, чьё окно истекло.
    PRUNE_AT = 10_000

    def failure(self, email: str) -> None:
        self._recent(email)
        if len(self._failures) >= self.PRUNE_AT:
            self._prune()
        self._failures.setdefault(self._key(email), deque()).append(self.clock())

    def _prune(self) -> None:
        """Убирает email, у которых все неудачи старше окна (память не растёт)."""
        now = self.clock()
        stale = [key for key, times in self._failures.items() if not times or now - times[-1] >= self.window]
        for key in stale:
            del self._failures[key]

    def reset(self, email: str) -> None:
        self._failures.pop(self._key(email), None)


async def session_refresh(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Middleware: продлённую на сервере сессию продлеваем и в браузере.

    Иначе cookie с фиксированным ``max_age`` истёк бы через 30 дней после
    входа, хотя ученик заходит каждый день.
    """
    response = await call_next(request)
    token = session_token(request)
    already_set = any(
        name.lower() == b"set-cookie" for name, _ in response.raw_headers
    )
    if token and getattr(request.state, "session_extended", False) and not already_set:
        set_session_cookie(response, token, request.app.state.settings)
    return response
