"""API админки: действия над аккаунтами (срезы 66, 69).

Защита стоит на роутере, а не в каждой функции: новый маршрут не может её
забыть. Изменяющие запросы идут через общий ``api_guard`` (только JSON и свой
``Origin``).

Секреты: временный пароль уходит только в ответе на сброс (``no-store``) и
нигде не пишется — ни в лог, ни в ``admin_log``, ни в исключения.
"""

import asyncio
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from llm_tutor.web import accounts
from llm_tutor.web.accounts import AccountRow, User
from llm_tutor.web.security import accounts_db, admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])

STUDENT_NOT_FOUND = "Ученик не найден."
SELF_ACTION = "Над своим аккаунтом это действие недоступно: пароль меняется в профиле, а снять роль с себя нельзя."

# 12 случайных байт в base64url — 16 символов, ~96 бит.
_PASSWORD_BYTES = 12


def _target(request: Request, admin: User, user_id: int) -> AccountRow:
    """Аккаунт, над которым действие: должен существовать и не быть самим админом.

    Иначе админ закрыл бы собственную сессию или сменил себе пароль в обход
    проверки текущего.
    """
    account = accounts.get_account(accounts_db(request), user_id)
    if account is None:
        raise HTTPException(status_code=404, detail={"message": STUDENT_NOT_FOUND})
    if account.id == admin.id:
        raise HTTPException(status_code=400, detail={"message": SELF_ACTION})
    return account


@router.post("/students/{user_id}/reset-password")
async def reset_password(
    user_id: int, request: Request, admin: User = Depends(admin_user)
) -> JSONResponse:
    """Новый временный пароль; все сессии ученика закрываются."""
    account = _target(request, admin, user_id)
    db = accounts_db(request)
    password = secrets.token_urlsafe(_PASSWORD_BYTES)
    # scrypt — в потоке: цикл событий обслуживает ходы всех учеников.
    password_hash = await asyncio.to_thread(accounts.hash_password, password)
    accounts.set_password(db, account.id, password_hash, keep_token=None)
    # Ученик, заблокированный лимитером после неудачных входов, должен войти.
    request.app.state.login_limiter.reset(account.email)
    accounts.log_admin(db, admin.id, "reset_password", account.id)
    logger.info("Админ %s сбросил пароль аккаунта %s", admin.id, account.id)
    return JSONResponse({"password": password}, headers={"Cache-Control": "no-store"})


@router.post("/students/{user_id}/close-sessions")
async def close_sessions(
    user_id: int, request: Request, admin: User = Depends(admin_user)
) -> dict:
    """Закрывает все веб-сессии ученика; пароль не меняется."""
    account = _target(request, admin, user_id)
    db = accounts_db(request)
    closed = accounts.close_user_sessions(db, account.id)
    accounts.log_admin(db, admin.id, "close_sessions", account.id, {"closed": closed})
    logger.info("Админ %s закрыл сессии аккаунта %s (%s)", admin.id, account.id, closed)
    return {"closed": closed}


async def _set_role(request: Request, admin: User, user_id: int, value: bool) -> dict:
    """Выдаёт или снимает роль админа (срез 69). Повтор ничего не меняет.

    Над собой — 400 (через ``_target``): снять роль с себя значило бы
    остаться без админки, а выдать себе — бессмысленно. Снятая роль
    действует со следующего запроса того, у кого её сняли.
    """
    account = _target(request, admin, user_id)
    db = accounts_db(request)
    if account.is_admin != value:
        accounts.set_admin(db, account.id, value)
        accounts.log_admin(db, admin.id, "grant_admin" if value else "revoke_admin", account.id)
        logger.info(
            "Админ %s %s роль админа аккаунта %s", admin.id, "выдал" if value else "снял", account.id
        )
    return {"is_admin": value}


@router.post("/students/{user_id}/grant-admin")
async def grant_admin(user_id: int, request: Request, admin: User = Depends(admin_user)) -> dict:
    return await _set_role(request, admin, user_id, True)


@router.post("/students/{user_id}/revoke-admin")
async def revoke_admin(user_id: int, request: Request, admin: User = Depends(admin_user)) -> dict:
    return await _set_role(request, admin, user_id, False)
