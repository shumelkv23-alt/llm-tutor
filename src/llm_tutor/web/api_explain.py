"""API «Объясни новичку» (срез 88).

Ученик объясняет пройденный модуль новичку: старт, ход, наставник,
завершение и отчёт, согласие показывать сессии преподавателю. Изменяющие
ходы — под замком ученика, как ходы занятия. Ошибки ядра
(``core.explain.ExplainError``) превращаются здесь в статусы ответа.
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator

from llm_tutor.core import explain
from llm_tutor.db import repos
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.schemas import ExplainSession
from llm_tutor.web import views
from llm_tutor.web.api_lesson import LessonContext, lesson_context

router = APIRouter(prefix="/api")

ERRORS: dict[str, tuple[int, str]] = {
    "not_available": (409, "Этот модуль пока нельзя объяснять: сначала пройдите его целиком."),
    "other_active": (409, "Сначала завершите начатое объяснение другого модуля."),
    "not_active": (409, "Эта сессия уже завершена."),
    "not_found": (404, "Такой сессии нет."),
    "llm": (502, LLM_FAILURE_REPLY),
}


class StartBody(BaseModel):
    topic: StrictInt = Field(ge=1)


class TurnBody(BaseModel):
    session_id: StrictInt
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Реплика из одних пробелов — не ход: два вызова модели впустую.
        if not value.strip():
            raise ValueError("пустая реплика")
        return value


class MentorBody(BaseModel):
    session_id: StrictInt
    on: StrictBool


class FinishBody(BaseModel):
    session_id: StrictInt


class SettingsBody(BaseModel):
    share: StrictBool


def _fail(error: explain.ExplainError) -> HTTPException:
    status, message = ERRORS[error.code]
    return HTTPException(status_code=status, detail={"message": message})


def session_view(ctx: LessonContext, session: ExplainSession) -> dict:
    topic = repos.get_topic(ctx.conn, session.topic_id)
    return views.explain_session_view(
        session,
        repos.get_explain_turns(ctx.conn, session.id),
        topic.explain if topic else None,
        title=topic.title if topic else "",
        max_turns=ctx.prompts.limits.max_student_turns,
        lesson_names=(
            {concept.id: concept.name for concept in repos.get_concepts(ctx.conn)}
            if session.report
            else None
        ),
    )


def _history(ctx: LessonContext) -> list[dict]:
    titles = {topic.number: topic.title for topic in repos.get_topics(ctx.conn)}
    rows = []
    for session in repos.list_explain_sessions(ctx.conn):
        report = session.report
        rows.append(
            {
                "id": session.id,
                "topic": session.topic_id,
                "title": titles.get(session.topic_id, ""),
                "status": session.status,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "coverage": (
                    {"done": report.required_done, "total": report.required_total}
                    if report
                    else None
                ),
            }
        )
    return rows


@router.get("/courses/{course_id}/explain")
async def overview(ctx: LessonContext = Depends(lesson_context)) -> dict:
    """Модули, активная сессия, история и согласие. Брошенную сессию закрывает."""
    async with ctx.lock:
        explain.close_stale(ctx.conn, ctx.prompts, now=time.time(), settings=ctx.settings)
        modules = explain.availability(ctx.conn, settings=ctx.settings)
        active = repos.active_explain_session(ctx.conn)
        return {
            "modules": [
                {"topic": m.topic, "title": m.title, "available": m.available, "reason": m.reason}
                for m in modules
            ],
            "active": session_view(ctx, active) if active else None,
            "history": _history(ctx),
            "share": explain.share_enabled(ctx.conn),
        }


@router.post("/courses/{course_id}/explain/start")
async def start(body: StartBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    async with ctx.lock:
        try:
            session = explain.start(
                ctx.conn, ctx.prompts, topic_id=body.topic, now=time.time(), settings=ctx.settings
            )
        except explain.ExplainError as error:
            raise _fail(error) from error
        return {"session": session_view(ctx, session)}


@router.post("/courses/{course_id}/explain/turn")
async def turn(body: TurnBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    async with ctx.lock:
        try:
            session = await explain.turn(
                ctx.conn,
                ctx.client,
                ctx.prompts,
                session_id=body.session_id,
                text=body.text,
                now=time.time(),
                settings=ctx.settings,
            )
        except explain.ExplainError as error:
            raise _fail(error) from error
        view = session_view(ctx, session)
        return {"messages": view["messages"][-2:], "session": view}


@router.post("/courses/{course_id}/explain/mentor")
async def mentor(body: MentorBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    async with ctx.lock:
        try:
            session = explain.set_mentor(
                ctx.conn, session_id=body.session_id, on=body.on, now=time.time()
            )
        except explain.ExplainError as error:
            raise _fail(error) from error
        return {"session": session_view(ctx, session)}


@router.post("/courses/{course_id}/explain/finish")
async def finish(body: FinishBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    """Отчёт сессии; повторный вызов отдаёт тот же отчёт. Пустая сессия — ``null``."""
    async with ctx.lock:
        try:
            report = explain.finish(
                ctx.conn, session_id=body.session_id, now=time.time(), settings=ctx.settings
            )
        except explain.ExplainError as error:
            raise _fail(error) from error
        session = repos.get_explain_session(ctx.conn, body.session_id)
        return {
            "report": report.model_dump() if report else None,
            "session": session_view(ctx, session) if session else None,
        }


@router.get("/courses/{course_id}/explain/sessions/{session_id}")
async def session_detail(session_id: int, ctx: LessonContext = Depends(lesson_context)) -> dict:
    async with ctx.lock:
        session = repos.get_explain_session(ctx.conn, session_id)
        if session is None:
            raise _fail(explain.ExplainError("not_found"))
        return {"session": session_view(ctx, session)}


@router.post("/courses/{course_id}/explain/settings")
async def settings(body: SettingsBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    """«Показывать мои сессии преподавателю»: действует сразу."""
    async with ctx.lock:
        explain.set_share(ctx.conn, body.share)
        return {"share": explain.share_enabled(ctx.conn)}
