"""API аккаунта: регистрация, вход, выход, имя и пароль.

Ошибки ввода отдаются как ``{"detail": {"field": …, "message": …}}``: страница
показывает сообщение рядом с полем.
"""

import asyncio
import logging
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from llm_tutor.config import Settings
from llm_tutor.web import accounts, mailer
from llm_tutor.web.accounts import AccountError, EmailTaken, User
from llm_tutor.web.security import (
    TOO_MANY_ATTEMPTS,
    accounts_db,
    clear_session_cookie,
    require_user,
    session_token,
    set_session_cookie,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

REGISTRATION_CLOSED = "Регистрация закрыта. Обратитесь к администратору."
WRONG_CREDENTIALS = "Неверный email или пароль."
TOO_MANY_RESETS = "Слишком много запросов письма. Подождите 15 минут и попробуйте снова."
RESET_SUBJECT = "Восстановление пароля в llm·tutor"

# Верхние границы — только против мусора: настоящие проверки в accounts.
_LONG = 1000


class RegisterBody(BaseModel):
    email: str = Field(max_length=_LONG)
    name: str = Field(max_length=_LONG)
    password: str = Field(max_length=_LONG)


class LoginBody(BaseModel):
    email: str = Field(max_length=_LONG)
    password: str = Field(max_length=_LONG)


class RenameBody(BaseModel):
    name: str = Field(max_length=_LONG)


class PasswordBody(BaseModel):
    current: str = Field(max_length=_LONG)
    new: str = Field(max_length=_LONG)


class ForgotPasswordBody(BaseModel):
    email: str = Field(max_length=_LONG)


class ResetPasswordBody(BaseModel):
    token: str = Field(max_length=_LONG)
    password: str = Field(max_length=_LONG)


def reset_link(settings: Settings, token: str) -> str:
    """Абсолютная ссылка восстановления — из PUBLIC_BASE_URL.

    Не из ``request.base_url``: за прокси заголовок Host подконтролен клиенту,
    и в письмо ушла бы ссылка на чужой хост.
    """
    return f"{settings.public_base_url.rstrip('/')}/reset-password?token={quote(token, safe='')}"


def reset_letter(user: User, link: str, ttl_minutes: int) -> str:
    """Текст письма: что случилось, сколько живёт ссылка и что делать, если это не вы."""
    return (
        f"Здравствуйте, {user.name}!\n\n"
        "Кто-то запросил восстановление пароля в llm·tutor для этого адреса.\n"
        f"Ссылка действует {ttl_minutes} мин и сработает один раз:\n\n"
        f"{link}\n\n"
        "Пароль изменится только после того, как вы откроете ссылку и зададите новый.\n"
        "Если восстановление запросили не вы — просто не открывайте её.\n"
    )


def user_view(user: User) -> dict[str, str]:
    return {"email": user.email, "name": user.name, "initial": user.initial}


def _field_error(error: AccountError, status_code: int = 422) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"field": error.field, "message": error.message}
    )


@router.post("/auth/register", status_code=201)
async def register(body: RegisterBody, request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    if not settings.registration_open:
        raise HTTPException(status_code=403, detail={"message": REGISTRATION_CLOSED})
    db = accounts_db(request)
    try:
        accounts.normalize_email(body.email)
        accounts.check_password(body.password)
        # scrypt — в потоке: цикл событий обслуживает ходы всех учеников.
        password_hash = await asyncio.to_thread(accounts.hash_password, body.password)
        user = accounts.create_user(
            db, email=body.email, name=body.name, password=body.password, password_hash=password_hash
        )
    except EmailTaken as error:
        raise _field_error(error, 409) from error
    except AccountError as error:
        raise _field_error(error) from error
    set_session_cookie(response, accounts.create_session(db, user.id), settings)
    return {"user": user_view(user)}


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    limiter = request.app.state.login_limiter
    if limiter.blocked(body.email):
        raise HTTPException(status_code=429, detail={"message": TOO_MANY_ATTEMPTS})
    db = accounts_db(request)
    user = None
    if len(body.password) <= accounts.MAX_PASSWORD:
        candidate, stored = accounts.credentials(db, body.email)
        if await asyncio.to_thread(accounts.verify_password, body.password, stored):
            user = candidate
    if user is None:
        limiter.failure(body.email)
        raise HTTPException(
            status_code=401, detail={"field": "password", "message": WRONG_CREDENTIALS}
        )
    limiter.reset(body.email)
    set_session_cookie(response, accounts.create_session(db, user.id), request.app.state.settings)
    return {"user": user_view(user)}


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    token = session_token(request)
    if token:
        accounts.delete_session(accounts_db(request), token)
    clear_session_cookie(response, request.app.state.settings)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(require_user)) -> dict:
    return {"user": user_view(user)}


@router.post("/me")
async def rename(body: RenameBody, request: Request, user: User = Depends(require_user)) -> dict:
    try:
        renamed = accounts.rename(accounts_db(request), user.id, body.name)
    except AccountError as error:
        raise _field_error(error) from error
    return {"user": user_view(renamed)}


@router.post("/me/password")
async def change_password(
    body: PasswordBody, request: Request, user: User = Depends(require_user)
) -> dict:
    # Перехваченная сессия не должна позволять подбирать текущий пароль.
    limiter = request.app.state.login_limiter
    if limiter.blocked(user.email):
        raise HTTPException(status_code=429, detail={"message": TOO_MANY_ATTEMPTS})
    db = accounts_db(request)
    stored = accounts.password_hash_of(db, user.id)
    if not await asyncio.to_thread(accounts.verify_password, body.current, stored):
        limiter.failure(user.email)
        raise HTTPException(
            status_code=422,
            detail={"field": "current", "message": accounts.CURRENT_PASSWORD_WRONG},
        )
    try:
        accounts.check_password(body.new, field="new")
    except AccountError as error:
        raise _field_error(error) from error
    password_hash = await asyncio.to_thread(accounts.hash_password, body.new)
    accounts.set_password(db, user.id, password_hash, keep_token=session_token(request))
    limiter.reset(user.email)
    return {"ok": True}


@router.post("/auth/forgot-password")
async def forgot_password(
    body: ForgotPasswordBody, request: Request, background: BackgroundTasks
) -> dict:
    """Письмо со ссылкой восстановления пароля.

    Ответ одинаков для зарегистрированного, неизвестного и кривого адреса:
    иначе по коду ответа можно было бы перебирать, кто здесь учится. Текст
    «проверьте почту» живёт в шаблоне страницы, а не в ответе.
    """
    settings = request.app.state.settings
    limiter = request.app.state.reset_limiter
    if limiter.blocked(body.email):
        raise HTTPException(status_code=429, detail={"message": TOO_MANY_RESETS})
    # Счётчик растёт для любого адреса: блокировка по «известным» адресам
    # сама выдавала бы, кто здесь учится.
    limiter.failure(body.email)
    db = accounts_db(request)
    user = accounts.find_user(db, body.email)
    if user is not None:
        token = accounts.create_reset_token(
            db, user.id, ttl=settings.password_reset_ttl_minutes * 60
        )
        # Письмо — фоном, после ответа: медленный SMTP не держит ученика,
        # а его отказ не ломает запрос.
        background.add_task(
            mailer.send_safely,
            request.app.state.mailer,
            to=user.email,
            subject=RESET_SUBJECT,
            text=reset_letter(
                user, reset_link(settings, token), settings.password_reset_ttl_minutes
            ),
        )
    return {"ok": True}


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordBody, request: Request, response: Response) -> dict:
    """Смена пароля по одноразовой ссылке из письма.

    Лимитера здесь нет намеренно: токен — 32 случайных байта, перебор
    невозможен, а счётчик мешал бы честной опечатке. Изменяющий запрос и так
    закрыт ``api_guard`` (чужой Origin — 403, не-JSON — 415).
    """
    settings = request.app.state.settings
    db = accounts_db(request)
    try:
        accounts.check_password(body.password)
    except AccountError as error:
        # Проверка до похода в БД: слабый пароль не должен сжигать ссылку —
        # ученик исправит опечатку и отправит ту же форму снова.
        raise _field_error(error) from error
    user = accounts.reset_token_user(db, body.token)
    if user is None:
        raise HTTPException(
            status_code=422,
            detail={"field": "token", "message": accounts.RESET_TOKEN_INVALID},
        )
    # scrypt — в потоке: цикл событий обслуживает ходы всех учеников.
    password_hash = await asyncio.to_thread(accounts.hash_password, body.password)
    try:
        accounts.reset_password(db, body.token, password_hash)
    except AccountError as error:
        # Гонку двух переходов по одной ссылке выиграл другой запрос.
        raise _field_error(error) from error
    # Прежние неудачные попытки входа — уже история: пароль новый.
    request.app.state.login_limiter.reset(user.email)
    clear_session_cookie(response, settings)
    logger.info("Пароль восстановлен по ссылке: ученик %s", user.id)
    return {"ok": True}
