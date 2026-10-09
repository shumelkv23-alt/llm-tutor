"""Вход в курс: приветствие, «С возвращением» и начало урока.

Нового ученика встречает одно сообщение с кнопкой «▶️ Поехали» — анкета живёт
в нём же (``bot/survey.py``). После анкеты бот показывает ближайшие шаги
списком и сразу начинает урок по первому из них.
"""

import logging
import sqlite3
from collections.abc import Collection

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from llm_tutor.bot import menu, render
from llm_tutor.bot.chat_action import typing_action
from llm_tutor.config import Settings
from llm_tutor.core.turn import TurnReply, reset_lesson, resume_reply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.prompts import BOT_FAILURE_REPLY
from llm_tutor.schemas import SessionState, Topic
from llm_tutor.student import route as route_mod
from llm_tutor.student import survey

logger = logging.getLogger(__name__)

# Текст reply-кнопки прошлой версии: у старых учеников она ещё висит внизу, и
# её нажатие должно вести на вход, а не в тьюторский ход.
START_LABEL = "▶️ Старт"

# Приветствие — два предложения: кто я и что сейчас будет. Что нажать, видно
# по единственной кнопке под ним.
INTRO_TEXT = (
    "👋 Привет! Я тьютор по курсу mlcourse.ai: 5 модулей, от pandas до случайного леса.\n"
    "Пара коротких вопросов — и подберу, с чего начать."
)

LESSON_LEAD = "Начинаем с «{name}» — сейчас коротко объясню и покажу пример."
CHECK_LEAD = "Всё отмечено знакомым — начнём с короткой проверки."
# Первая тема знакома по анкете, но впереди есть и новое (прыжок из меню).
CLAIMED_LEAD = "«{name}» в анкете отмечена знакомой — начнём с короткой проверки."

# Вход в модуль 2–10: что за модуль и что сейчас будет (сырой текст).
MODULE_INTRO_TEMPLATE = (
    "📘 Модуль {number} · {title}\n"
    "{intro}\n"
    "Пара коротких вопросов — и подберу, с чего начать."
)

WELCOME_BACK_TEMPLATE = "👋 С возвращением! Модуль {number} «{topic}» — продолжаем «{name}»."
WELCOME_BACK_IDLE = "👋 С возвращением! Напиши что угодно — продолжим."


def welcome_back_text(conn: sqlite3.Connection) -> str:
    """«С возвращением» с текущей темой — без модели (сырой текст).

    Это чтение, а не ход: сессию не заводим (её заведёт ``handle_start``).
    """
    session_id = repos.get_open_session(conn)
    if session_id is None:
        return WELCOME_BACK_IDLE
    state = repos.get_session_state(conn, session_id)
    node_id = state.current_node_id
    graph = CourseGraph.load(conn)
    if node_id is None or not graph.has_node(node_id):
        return WELCOME_BACK_IDLE
    # Модуль — тот же, что в /plan: тема прошлого модуля бывает в участке
    # текущего (финальное ревью ветки, F-4).
    topic_id = route_mod.topic_for(graph, state)
    topic = repos.get_topic(conn, topic_id) if topic_id is not None else None
    name = graph.concept(node_id).name
    if topic is None:
        return f"👋 С возвращением! Продолжаем «{name}»."
    return WELCOME_BACK_TEMPLATE.format(number=topic.number, topic=topic.title, name=name)


def module_intro_text(topic: Topic) -> str:
    """Приветствие анкеты модуля (сырой текст)."""
    return MODULE_INTRO_TEMPLATE.format(
        number=topic.number, title=topic.title, intro=topic.intro
    )


def pending_survey_topic(conn: sqlite3.Connection) -> Topic | None:
    """Модуль занятия, анкета которого ещё не пройдена (§5.2), иначе ``None``.

    Это чтение, а не ход: сессию не заводим.
    """
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return None
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    topic_id = route_mod.topic_for(graph, state)
    topic = repos.get_topic(conn, topic_id) if topic_id is not None else None
    if topic is None or survey.is_completed(conn, topic.survey):
        return None
    return topic


async def begin_lesson(
    message: Message,
    state: FSMContext,
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    settings: Settings,
    *,
    claimed: Collection[str] = (),
    start_node_id: str | None = None,
) -> None:
    """Список ближайших шагов и первый ход урока.

    Экран согласования маршрута убран: показали, что впереди, — и сразу
    начинаем первую тему. FSM-состояние анкеты снимаем: дальше занятие.
    ``claimed`` — темы, которые анкета модуля только что назвала знакомыми.
    ``start_node_id`` — тема, выбранная в меню до анкеты (прыжок в модуль).
    """
    await state.clear()
    session_id = repos.get_open_session(conn)
    if start_node_id is None and session_id is not None:
        # FSM потерян (/start посреди анкеты, рестарт) — тема прыжка из сессии.
        start_node_id = repos.get_session_state(conn, session_id).jump_node_id
    reset_lesson(conn, claimed=claimed)
    graph = CourseGraph.load(conn)
    previous = repos.get_session_state(conn, session_id).route if session_id else None
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        previous=previous,
        settings=settings,
    )
    # Первый шаг списка — ровно тот узел, с которого начнётся урок: его же
    # передаём в resume_reply, иначе планировщик мог бы выбрать другой.
    first = (
        start_node_id
        if start_node_id is not None and graph.has_node(start_node_id)
        else route_mod.next_node_id(conn, graph, route, settings=settings)
    )
    # Список — по участку модуля работы: первой темой может быть подтянутый
    # предок из прошлого модуля, и участок его модуля был бы не тот.
    topic = (
        route.topic_id
        if start_node_id is None and route.topic_id is not None
        else graph.topic_of(first) if first is not None else None
    )
    section = route_mod.working_section(graph, route, topic) if first is not None else []
    upcoming = route_mod.upcoming(route, first, limit=render.PLAN_STEPS, section=section)
    if not upcoming:
        await message.answer(
            "Всё доступное уже освоено — можно свериться: /plan.",
            reply_markup=menu.main_menu(),
        )
        return

    head = (
        f"📋 Ближайшие {len(upcoming)} шагов:"
        if len(upcoming) == render.PLAN_STEPS
        else "📋 Что впереди:"
    )
    name = render.escape(graph.concept(first).name)
    if upcoming[0].status != "claimed":
        lead = LESSON_LEAD.format(name=name)
    elif all(step.status == "claimed" for step in upcoming):
        lead = CHECK_LEAD
    else:
        # «Всё отмечено» было бы неправдой: впереди и незнакомое (F-5).
        lead = CLAIMED_LEAD.format(name=name)
    await message.answer(
        f"{head}\n\n{render.render_steps(graph, upcoming)}\n\n{lead}",
        parse_mode=render.PARSE_MODE,
        reply_markup=menu.main_menu(),
    )
    # Локальный импорт: handlers импортирует start на уровне модуля, и общий
    # импорт дал бы цикл.
    from llm_tutor.bot.handlers import _send_reply

    try:
        async with typing_action(message):
            reply = await resume_reply(
                conn, client, model, settings=settings, start_node_id=first
            )
    except Exception:  # noqa: BLE001 — после «сейчас объясню» молчать нельзя
        logger.exception("Сбой первого хода урока")
        reply = TurnReply(text=BOT_FAILURE_REPLY)
    await _send_reply(conn, message, reply)
