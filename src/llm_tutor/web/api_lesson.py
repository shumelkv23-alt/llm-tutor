"""API курсов и занятия.

Изменяющие ходы занятия идут под замком ученика (``UserDBPool.lock``): двойной
клик или вторая вкладка дождутся первого хода, а не перемешают состояние.
Каждый ход отвечает репликами для чата и свежим состоянием занятия.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictInt

from llm_tutor.config import Settings
from llm_tutor.core import lesson, turn, verify
from llm_tutor.core.turn import ROUTE_DONE_REPLY, TurnReply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.prompts import BOT_FAILURE_REPLY, LLM_FAILURE_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import survey
from llm_tutor.web import accounts, views
from llm_tutor.web.accounts import User
from llm_tutor.web.courses import COURSES, Course, get_course
from llm_tutor.web.security import accounts_db, require_user
from llm_tutor.web.views import course_card

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

COURSE_NOT_FOUND = "Такого курса нет."


def course_or_404(course_id: str) -> Course:
    course = get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail={"message": COURSE_NOT_FOUND})
    return course


def cards_for(request: Request, user: User) -> list[dict]:
    enrolled = set(accounts.enrolled_courses(accounts_db(request), user.id))
    return [
        course_card(
            course,
            user_id=user.id,
            enrolled=course.id in enrolled,
            pool=request.app.state.userdbs,
            settings=request.app.state.settings,
        )
        for course in COURSES
    ]


@router.get("/courses")
async def list_courses(request: Request, user: User = Depends(require_user)) -> dict:
    return {"courses": cards_for(request, user)}


@router.post("/courses/{course_id}/enroll")
async def enroll(course_id: str, request: Request, user: User = Depends(require_user)) -> dict:
    course = course_or_404(course_id)
    pool = request.app.state.userdbs
    async with pool.lock(user.id, course.id):
        # Сначала БД ученика: запись без БД оставила бы курс, который не открыть.
        pool.open(user.id, course.id)
        accounts.enroll(accounts_db(request), user.id, course.id)
    card = course_card(
        course, user_id=user.id, enrolled=True, pool=pool, settings=request.app.state.settings
    )
    return {"course": card}


# --- Занятие ---

STALE_ITEM_STATUS = 409
NOT_ENROLLED = "Сначала запишитесь на курс."
SURVEY_DONE = "Анкета уже пройдена."
SURVEY_NOT_DONE = "Сначала пройдите анкету."
SURVEY_INVALID = "Ответы анкеты не сходятся с вопросами — начните её заново."
NEW_TASK_NOTE = "📝 Задание — во вкладке «Практика»."


@dataclass
class LessonContext:
    """Всё, что нужно ходу: ученик, курс, его БД, настройки и модель."""

    user: User
    course: Course
    conn: sqlite3.Connection
    lock: asyncio.Lock
    settings: Settings
    client: LLMClient

    @property
    def model(self) -> str:
        return self.settings.tutor_model


async def lesson_context(
    course_id: str, request: Request, user: User = Depends(require_user)
) -> LessonContext:
    course = course_or_404(course_id)
    if course.id not in accounts.enrolled_courses(accounts_db(request), user.id):
        raise HTTPException(status_code=403, detail={"message": NOT_ENROLLED})
    pool = request.app.state.userdbs
    return LessonContext(
        user=user,
        course=course,
        conn=pool.open(user.id, course.id),
        lock=pool.lock(user.id, course.id),
        settings=request.app.state.settings,
        client=request.app.state.client,
    )


def turn_response(ctx: LessonContext, user_text: str | None, reply: TurnReply | str) -> dict:
    """Ответ хода: реплики для чата и свежее состояние занятия.

    Задание в чат не идёт (см. инвариант ``TurnReply``): оно в состоянии, его
    рисует «Практика». Если ход состоял из одного задания, в чат — короткая
    отметка, чтобы ученик видел, что ход случился.
    """
    messages = []
    if user_text:
        messages.append(views.message_view("user", user_text))
    if isinstance(reply, str):
        text = reply
    elif reply.item_id is not None:
        text = f"{reply.text}\n\n{NEW_TASK_NOTE}" if reply.text else NEW_TASK_NOTE
    else:
        text = reply.text if not reply.tail else f"{reply.text}\n\n{reply.tail}"
    if text:
        messages.append(views.message_view("assistant", text))
    return {"messages": messages, "state": views.lesson_state(ctx.conn, settings=ctx.settings)}


def _require_survey(ctx: LessonContext) -> None:
    if not survey.is_completed(ctx.conn):
        raise HTTPException(status_code=409, detail={"message": SURVEY_NOT_DONE})


async def _safe(call: Awaitable[TurnReply], failure: str) -> TurnReply:
    """Сбой ядра — реплика вместо 500: ученик не должен остаться без ответа."""
    try:
        return await call
    except Exception:  # noqa: BLE001 — интерфейс не должен молчать
        logger.exception("Сбой хода занятия")
        return TurnReply(text=failure)


async def _sync(func, *args, **kwargs):
    return func(*args, **kwargs)


class SurveyBody(BaseModel):
    answers: list[StrictInt] = Field(max_length=32)


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class AnswerBody(BaseModel):
    item_id: StrictInt
    # Номер варианта для choice или текст ответа.
    answer: StrictInt | str


class SwitchBody(BaseModel):
    node_id: str = Field(max_length=200)


@router.get("/courses/{course_id}/lesson/state")
async def lesson_state(ctx: LessonContext = Depends(lesson_context)) -> dict:
    return {"state": views.lesson_state(ctx.conn, settings=ctx.settings)}


@router.post("/courses/{course_id}/lesson/survey/next")
async def survey_next(body: SurveyBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    if survey.is_completed(ctx.conn):
        raise HTTPException(status_code=409, detail={"message": SURVEY_DONE})
    try:
        progress = views.survey_progress(body.answers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"message": SURVEY_INVALID}) from exc
    if progress.next_key() is None:
        return {"question": None, "summary": views.survey_summary(progress.final_answers())}
    return {"question": views.survey_question(progress), "summary": None}


@router.post("/courses/{course_id}/lesson/survey/finish")
async def survey_finish(body: SurveyBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    try:
        progress = views.survey_progress(body.answers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"message": SURVEY_INVALID}) from exc
    if progress.next_key() is not None:
        raise HTTPException(status_code=422, detail={"message": SURVEY_INVALID})
    async with ctx.lock:
        # Проверка под замком: второй параллельный finish увидит пройденную анкету.
        if survey.is_completed(ctx.conn):
            raise HTTPException(status_code=409, detail={"message": SURVEY_DONE})
        final = progress.final_answers()
        survey.apply_answers(ctx.conn, final, level=progress.level, settings=ctx.settings)
        try:
            start, reply = await lesson.begin_lesson(
                ctx.conn, ctx.client, ctx.model, settings=ctx.settings
            )
        except Exception:  # noqa: BLE001 — анкета записана, урок продолжится ходом
            logger.exception("Сбой первого хода урока")
            start, reply = None, TurnReply(text=BOT_FAILURE_REPLY)
        response = turn_response(ctx, None, reply or TurnReply(text=ROUTE_DONE_REPLY))
    lead = _lesson_lead(ctx.conn, start) if start is not None else None
    if lead:
        response["messages"].insert(0, views.message_view("assistant", lead))
    response["summary"] = views.survey_summary(final)
    return response


def _lesson_lead(conn: sqlite3.Connection, start: lesson.LessonStart) -> str | None:
    """«Ближайшие шаги» и подводка к первому уроку."""
    if not start.upcoming or start.first is None:
        return None
    graph = CourseGraph.load(conn)
    steps = "\n".join(
        f"{index}. {graph.concept(step.concept_id).name}"
        for index, step in enumerate(start.upcoming, start=1)
    )
    template = lesson.CHECK_LEAD if start.starts_with_check else lesson.LESSON_LEAD
    lead = template.format(name=graph.concept(start.first).name)
    return f"**Ближайшие шаги**\n\n{steps}\n\n{lead}"


@router.post("/courses/{course_id}/lesson/chat")
async def chat(body: ChatBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail={"field": "text", "message": "Пустое сообщение."})
    async with ctx.lock:
        reply = await _safe(
            turn.handle_turn(ctx.conn, ctx.client, ctx.model, text, settings=ctx.settings),
            LLM_FAILURE_REPLY,
        )
        return turn_response(ctx, text, reply)


@router.post("/courses/{course_id}/lesson/answer")
async def answer(body: AnswerBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    async with ctx.lock:
        session_id = repos.get_open_session(ctx.conn)
        state = repos.get_session_state(ctx.conn, session_id) if session_id else SessionState()
        item = repos.get_item(ctx.conn, state.pending_item_id) if state.pending_item_id else None
        if item is None or item.id != body.item_id:
            raise HTTPException(
                status_code=STALE_ITEM_STATUS, detail={"message": turn.STALE_ITEM_REPLY}
            )
        if isinstance(body.answer, int):
            if item.answer_type != "choice" or not 0 <= body.answer < len(item.options):
                raise HTTPException(status_code=422, detail={"message": "Нет такого варианта."})
            # Как в боте: ответ — подпись варианта, намерения из неё не ловим.
            text = item.options[body.answer]
        else:
            text = body.answer.strip()
            if not text or len(text) > 8000:
                raise HTTPException(
                    status_code=422, detail={"field": "answer", "message": "Пустой ответ."}
                )
        reply = await _safe(
            turn.handle_turn(
                ctx.conn, ctx.client, ctx.model, text, settings=ctx.settings, allow_intents=False
            ),
            LLM_FAILURE_REPLY,
        )
        return turn_response(ctx, text, reply)


@router.post("/courses/{course_id}/lesson/task")
async def take_task(ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    async with ctx.lock:
        reply = await _safe(
            _sync(turn.start_practice_reply, ctx.conn, settings=ctx.settings), BOT_FAILURE_REPLY
        )
        return turn_response(ctx, turn.PRACTICE_KICKOFF_TEXT, reply)


@router.post("/courses/{course_id}/lesson/skip")
async def skip(ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    async with ctx.lock:
        try:
            text = turn.skip_pending(ctx.conn, settings=ctx.settings)
        except Exception:  # noqa: BLE001
            logger.exception("Сбой пропуска задания")
            text = BOT_FAILURE_REPLY
        return turn_response(ctx, turn.SKIP_KICKOFF_TEXT, text)


@router.post("/courses/{course_id}/lesson/stuck")
async def stuck(ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    async with ctx.lock:
        reply = await _safe(
            turn.stuck_reply(ctx.conn, ctx.client, ctx.model, settings=ctx.settings),
            LLM_FAILURE_REPLY,
        )
        return turn_response(ctx, turn.STUCK_KICKOFF_TEXT, reply)


@router.post("/courses/{course_id}/lesson/resume")
async def resume(ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    async with ctx.lock:
        reply = await _safe(
            turn.resume_reply(ctx.conn, ctx.client, ctx.model, settings=ctx.settings),
            LLM_FAILURE_REPLY,
        )
        return turn_response(ctx, turn.RESUME_KICKOFF_TEXT, reply)


@router.post("/courses/{course_id}/lesson/verify")
async def verify_topic(ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    async with ctx.lock:
        reply = await _safe(
            _sync(verify.start_verification, ctx.conn, settings=ctx.settings), BOT_FAILURE_REPLY
        )
        return turn_response(ctx, verify.VERIFY_KICKOFF_TEXT, reply)


@router.post("/courses/{course_id}/lesson/switch")
async def switch(body: SwitchBody, ctx: LessonContext = Depends(lesson_context)) -> dict:
    _require_survey(ctx)
    async with ctx.lock:
        graph = CourseGraph.load(ctx.conn)
        if not graph.has_node(body.node_id):
            raise HTTPException(status_code=404, detail={"message": "Такой темы нет."})
        try:
            text = lesson.switch_node(ctx.conn, body.node_id, settings=ctx.settings)
        except Exception:  # noqa: BLE001
            logger.exception("Сбой перехода к теме")
            text = BOT_FAILURE_REPLY
        name = graph.concept(body.node_id).name
        return turn_response(ctx, f"Перейти к теме «{name}»", text)
