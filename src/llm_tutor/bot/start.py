"""Вход в курс: приветствие, «С возвращением» и начало урока.

Нового ученика встречает одно сообщение с кнопкой «▶️ Поехали» — анкета живёт
в нём же (``bot/survey.py``). После анкеты бот показывает ближайшие шаги
списком и сразу начинает урок по первому из них.
"""

import logging
import sqlite3

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from llm_tutor.bot import menu, render
from llm_tutor.bot.chat_action import typing_action
from llm_tutor.config import Settings
from llm_tutor.core.turn import TurnReply, resume_reply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.prompts import BOT_FAILURE_REPLY
from llm_tutor.student import route as route_mod

logger = logging.getLogger(__name__)

# Текст reply-кнопки прошлой версии: у старых учеников она ещё висит внизу, и
# её нажатие должно вести на вход, а не в тьюторский ход.
START_LABEL = "▶️ Старт"

# Приветствие — два предложения: кто я и что сейчас будет. Что нажать, видно
# по единственной кнопке под ним.
INTRO_TEXT = (
    "👋 Привет! Я тьютор по теме «Pandas / EDA» курса mlcourse.ai.\n"
    "Пара коротких вопросов — и подберу, с чего начать."
)

LESSON_LEAD = "Начинаем с «{name}» — сейчас коротко объясню и покажу пример."
CHECK_LEAD = "Начинаем с проверки «{name}» — пара быстрых вопросов."

WELCOME_BACK_TEMPLATE = "👋 С возвращением! Продолжаем «{name}»."
WELCOME_BACK_IDLE = "👋 С возвращением! Напиши что угодно — продолжим."


def welcome_back_text(conn: sqlite3.Connection) -> str:
    """«С возвращением» с текущей темой — без модели (сырой текст).

    Это чтение, а не ход: сессию не заводим (её заведёт ``handle_start``).
    """
    session_id = repos.get_open_session(conn)
    if session_id is None:
        return WELCOME_BACK_IDLE
    node_id = repos.get_session_state(conn, session_id).current_node_id
    graph = CourseGraph.load(conn)
    if node_id is None or not graph.has_node(node_id):
        return WELCOME_BACK_IDLE
    return WELCOME_BACK_TEMPLATE.format(name=graph.concept(node_id).name)


async def begin_lesson(
    message: Message,
    state: FSMContext,
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    settings: Settings,
) -> None:
    """Список ближайших шагов и первый ход урока.

    Экран согласования маршрута убран: показали, что впереди, — и сразу
    начинаем первую тему. FSM-состояние анкеты снимаем: дальше занятие.
    """
    await state.clear()
    graph = CourseGraph.load(conn)
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        settings=settings,
    )
    # Первый шаг списка — ровно тот узел, с которого начнётся урок: его же
    # передаём в resume_reply, иначе планировщик мог бы выбрать другой.
    first = route_mod.next_node_id(conn, graph, route, settings=settings)
    upcoming = route_mod.upcoming(route, first, limit=render.PLAN_STEPS)
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
    lead = (CHECK_LEAD if upcoming[0].status == "claimed" else LESSON_LEAD).format(name=name)
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
