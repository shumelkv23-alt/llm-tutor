"""Публичная отписка от напоминаний по токену из письма.

Токен подписан HMAC и не требует входа: письмо могло прийти на адрес, а сессия
к этому моменту истекла. Отписка выключает только письма (``review_reminders``),
повторение в курсе остаётся.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from llm_tutor.web import accounts, reminders
from llm_tutor.web.security import accounts_db

router = APIRouter(prefix="/api")


class UnsubscribeBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    # false — отписаться (кнопка из письма), true — включить письма снова.
    enabled: bool = False


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeBody, request: Request) -> dict:
    settings = request.app.state.settings
    user_id = reminders.verify_token(settings, body.token)
    if user_id is None:
        raise HTTPException(status_code=404, detail={"message": "Ссылка недействительна."})
    db = accounts_db(request)
    pool = request.app.state.userdbs
    changed = 0
    for course_id in accounts.enrolled_courses(db, user_id):
        if not pool.exists(user_id, course_id):
            continue
        conn = pool.open(user_id, course_id)
        reminders.set_reminders_enabled(conn, body.enabled)
        changed += 1
    return {"ok": True, "enabled": body.enabled, "courses": changed}
