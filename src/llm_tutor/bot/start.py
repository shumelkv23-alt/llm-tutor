"""Вход в курс: кнопка «▶️ Старт», приветствие и начало урока.

Нового ученика встречает одна кнопка и три предложения — без схемы маршрута и
без кнопок согласования. После анкеты бот показывает ближайшие шаги списком и
сразу начинает урок по первому из них.
"""

import logging
import sqlite3

from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from llm_tutor.bot import menu, render
from llm_tutor.config import Settings
from llm_tutor.core.turn import resume_reply
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMClient
from llm_tutor.student import route as route_mod

logger = logging.getLogger(__name__)

START_LABEL = "▶️ Старт"

# Приветствие — ровно три предложения: кто я, что будет, что нажать.
INTRO_TEXT = (
    "👋 Привет! Я тьютор по теме 1 курса mlcourse.ai — «Pandas / EDA».\n"
    "Задам несколько коротких вопросов о тебе — это меньше минуты.\n"
    f"Нажми «{START_LABEL}», когда будешь готов пройти опрос."
)

# Реплика до анкеты: маршрута ещё нет, вести занятие не по чему.
BEFORE_SURVEY_REPLY = f"Сначала пару вопросов о тебе — нажми «{START_LABEL}»."


def start_keyboard() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура нового ученика: одна кнопка «▶️ Старт»."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=START_LABEL)]],
        resize_keyboard=True,
        is_persistent=True,
    )


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
    upcoming = [
        step for step in route.steps if step.status != "closed"
    ][: render.PLAN_STEPS]
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
    first = render.escape(graph.concept(upcoming[0].concept_id).name)
    await message.answer(
        f"{head}\n\n{render.render_steps(graph, upcoming, marks=False)}\n\n"
        f"Начинаем с «{first}» — сейчас коротко объясню и покажу пример.",
        parse_mode=render.PARSE_MODE,
        reply_markup=menu.main_menu(),
    )
    reply = await resume_reply(conn, client, model, settings=settings)
    await message.answer(
        render.fit(render.escape(reply.text)),
        parse_mode=render.PARSE_MODE,
    )
