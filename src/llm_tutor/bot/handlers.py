"""Telegram-хендлеры бота.

Зависимости (соединение с БД, LLM-клиент, модель) внедряются через фабрику
`make_router`, чтобы логику можно было тестировать без живого aiogram.

Свободный текст идёт в ``core.turn.handle_turn`` — единый ход диалога
(ответ на задание либо тьюторский путь). Задания выдаются командой ``/task``,
ответ на них принимается кнопкой или текстом через состояние сессии в БД,
а не память процесса: рестарт бота заход не теряет.
"""

import logging
import sqlite3
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from llm_tutor.bot.survey import ask as ask_survey
from llm_tutor.config import Settings
from llm_tutor.core.turn import STALE_ITEM_REPLY, handle_turn, start_practice
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.db.repos import (
    add_message,
    ensure_open_session,
    get_fact,
    get_session_state,
    update_session_state,
)
from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY, LLM_FAILURE_REPLY
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.schemas import Item
from llm_tutor.student.planner import MODE_LABELS, ready_nodes
from llm_tutor.student.survey import GOAL_CONCEPT_KEY, is_completed as survey_completed

START_SYSTEM_PROMPT = (
    "Ты — тьютор по курсу машинного обучения (mlcourse.ai), тема 1 «Pandas / EDA». "
    "Поздоровайся коротко и предложи задать вопрос по теме."
)

# /start — команда, а не реплика ученика: в диалог пишем приветствие,
# чтобы история не засорялась литералом "/start".
START_GREETING = "Привет! Хочу начать учиться."

# Telegram отклоняет сообщения длиннее 4096 символов — оставляем запас.
MAX_REPLY_LENGTH = 4000

ANSWER_CALLBACK_PREFIX = "answer"

logger = logging.getLogger(__name__)


def _truncate(reply: str) -> str:
    """Режет ответ до безопасной длины сообщения Telegram."""
    if len(reply) > MAX_REPLY_LENGTH:
        return reply[:MAX_REPLY_LENGTH] + "…"
    return reply


async def build_start_reply(client: LLMClient, model: str, user_text: str) -> str:
    """Ответ на /start: приветствие модели. Сбои LLM ловятся здесь."""
    messages = [
        ChatMessage(role="system", content=START_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_text or "Привет!"),
    ]
    try:
        answer = await client.chat(messages, model=model)
    except LLMError:
        return LLM_FAILURE_REPLY
    return _truncate(f"[модель: {model}]\n\n{answer}")


def _persist_turn(
    conn: sqlite3.Connection, session_id: int, user_text: str, reply: str, ts: float
) -> None:
    """Пишет реплики хода и обновляет ``last_activity`` сессии."""
    add_message(conn, session_id, "user", user_text, ts=ts)
    add_message(conn, session_id, "assistant", reply, ts=ts)
    state = get_session_state(conn, session_id)
    update_session_state(conn, session_id, state.model_copy(update={"last_activity": ts}))


async def handle_start(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    now: float | None = None,
) -> str:
    """Полный ход на /start: сессия + запись реплик + приветствие модели."""
    ts = time.time() if now is None else now
    reply = await build_start_reply(client, model, user_text)

    session_id = ensure_open_session(conn, ts)
    _persist_turn(conn, session_id, user_text, reply, ts)
    return reply


def _pending_item(conn: sqlite3.Connection) -> Item | None:
    """Задание, ответа на которое сейчас ждём (из состояния сессии)."""
    session_id = ensure_open_session(conn, time.time())
    state = get_session_state(conn, session_id)
    if state.pending_item_id is None:
        return None
    return repos.get_item(conn, state.pending_item_id)


def _answer_keyboard(item: Item | None) -> InlineKeyboardMarkup | None:
    """Кнопки вариантов — только для задания с вариантами."""
    if item is None or not item.options:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=option,
                    callback_data=f"{ANSWER_CALLBACK_PREFIX}:{index}",
                )
            ]
            for index, option in enumerate(item.options)
        ]
    )


def render_plan(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Текст ``/plan``: готовые узлы маршрута с режимом прохода."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY

    # Цель из анкеты учитываем, только если такой узел есть в графе
    # (seed мог поменяться между запусками).
    goal = get_fact(conn, GOAL_CONCEPT_KEY)
    if goal not in graph.node_ids:
        goal = None

    nodes = ready_nodes(conn, graph, goal_concept_id=goal, now=now, settings=settings)
    if not nodes:
        # Фронт готовности непуст всегда (корни без пререквизитов), поэтому
        # пустой маршрут = всё доступное освоено, а не «нет пререквизитов».
        return "Всё доступное уже освоено — можно двигаться дальше или взять цель посложнее."

    lines = ["Что можно взять сейчас:"]
    for node in nodes:
        name = graph.concept(node.concept_id).name
        lines.append(f"• {name} — {MODE_LABELS[node.mode]} (приоритет {node.priority:.2f})")
    return "\n".join(lines)


def make_router(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    settings: Settings | None = None,
) -> Router:
    """Собирает роутер с внедрёнными зависимостями (conn, client, model)."""
    router = Router()

    @router.message(CommandStart())
    async def on_start(message: Message, state: FSMContext) -> None:
        # Пока профиль не заполнен — сначала короткая анкета (Срез 4.7).
        if not survey_completed(conn):
            await ask_survey(message, state)
            return
        reply = await handle_start(conn, client, model, START_GREETING)
        await message.answer(reply)

    @router.message(Command("plan"))
    async def on_plan(message: Message) -> None:
        await message.answer(render_plan(conn, settings=settings))

    @router.message(Command("task"))
    async def on_task(message: Message) -> None:
        text = start_practice(conn, settings=settings)
        await message.answer(_truncate(text), reply_markup=_answer_keyboard(_pending_item(conn)))

    @router.callback_query(F.data.startswith(f"{ANSWER_CALLBACK_PREFIX}:"))
    async def on_answer(callback: CallbackQuery) -> None:
        # Ответ приходит из состояния сессии в БД — рестарт процесса его не теряет.
        item = _pending_item(conn)
        index = int((callback.data or "").split(":")[1])
        if item is None or not 0 <= index < len(item.options):
            await callback.message.answer(STALE_ITEM_REPLY)
            await callback.answer()
            return
        reply = await handle_turn(conn, client, model, item.options[index], settings=settings)
        await callback.message.answer(_truncate(reply))
        await callback.answer()

    # Обработчик свободного текста: не трогает команды (иначе CommandStop был
    # бы перехвачен) и не лезет в незавершённые FSM-потоки (анкета, диагностика) —
    # их шаги обрабатывают свои роутеры.
    @router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        reply = await handle_turn(conn, client, model, message.text or "", settings=settings)
        await message.answer(_truncate(reply))

    return router
