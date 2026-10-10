"""API аккаунта: регистрация, вход, выход, имя и пароль.

Ошибки ввода отдаются как ``{"detail": {"field": …, "message": …}}``: страница
показывает сообщение рядом с полем.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from llm_tutor.web import accounts
from llm_tutor.web.accounts import AccountError, EmailTaken, User
from llm_tutor.web.security import (
    TOO_MANY_ATTEMPTS,
    accounts_db,
    clear_session_cookie,
    require_user,
    session_token,
    set_session_cookie,
)

router = APIRouter(prefix="/api")

REGISTRATION_CLOSED = "Регистрация закрыта. Обратитесь к администратору."
WRONG_CREDENTIALS = "Неверный email или пароль."

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
