"""Telegram-хендлеры бота.

Зависимости (соединение с БД, LLM-клиент, модель) внедряются через фабрику
`make_router`, чтобы логику можно было тестировать без живого aiogram.
"""

import logging
import sqlite3
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from llm_tutor.bot.survey import ask as ask_survey
from llm_tutor.config import Settings
from llm_tutor.core.context import DEFAULT_DIALOG_TAIL, DEFAULT_RAG_TOP_K, build_context
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db.repos import (
    add_message,
    ensure_open_session,
    get_fact,
    get_session_state,
    update_session_state,
)
from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.prompts import NO_COURSE_ANSWER
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.rag.retriever import has_searchable_content
from llm_tutor.schemas import NodeMode
from llm_tutor.student.planner import ready_nodes
from llm_tutor.student.survey import GOAL_CONCEPT_KEY, is_completed as survey_completed

# Человекочитаемые названия режимов прохода узла (см. student/planner.py).
MODE_LABELS: dict[NodeMode, str] = {
    "skip": "пропустить",
    "verify": "проверить и идти дальше",
    "compressed": "сжатый проход",
    "full": "полный проход",
    "reinforce": "усиленный проход",
    "revisit": "вернуться к пререквизиту",
    "review": "повторение",
}

START_SYSTEM_PROMPT = (
    "Ты — тьютор по курсу машинного обучения (mlcourse.ai), тема 1 «Pandas / EDA». "
    "Поздоровайся коротко и предложи задать вопрос по теме."
)

# /start — команда, а не реплика ученика: в диалог пишем приветствие,
# чтобы история не засорялась литералом "/start".
START_GREETING = "Привет! Хочу начать учиться."

# Telegram отклоняет сообщения длиннее 4096 символов — оставляем запас.
MAX_REPLY_LENGTH = 4000

# Пока не удалось получить ответ — ученик не должен получать молчание.
LLM_FAILURE_REPLY = "Не смог получить ответ от модели — попробуй ещё раз чуть позже."

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
    """Полный ход на /start: сессия + запись реплик + приветствие модели.

    Порядок: сначала ответ модели, затем записи — сбой LLM не оставит
    «сиротскую» реплику ученика. Полная атомарность хода (единый коммит)
    появится в Срезе 5 вместе с ``post_turn``.
    """
    ts = time.time() if now is None else now
    reply = await build_start_reply(client, model, user_text)

    session_id = ensure_open_session(conn, ts)
    _persist_turn(conn, session_id, user_text, reply, ts)
    return reply


async def handle_message(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    rag_top_k: int = DEFAULT_RAG_TOP_K,
    dialog_tail: int = DEFAULT_DIALOG_TAIL,
    now: float | None = None,
) -> str:
    """Ход на свободный вопрос: RAG-контекст → ответ с опорой на материал курса.

    Если ретривер не нашёл чанка на осмысленный запрос — детерминированное
    «нет в курсе» без вызова LLM (дёшево и предсказуемо). Реплики без значимых
    токенов (приветствие, благодарность) идут к модели как обычный диалог.
    """
    ts = time.time() if now is None else now
    session_id = ensure_open_session(conn, ts)

    package = build_context(
        conn, session_id, user_text, top_k=rag_top_k, dialog_tail=dialog_tail
    )
    if not package.chunks and has_searchable_content(user_text):
        reply = NO_COURSE_ANSWER
    else:
        try:
            reply = await client.chat(package.messages, model=model)
        except LLMError:
            reply = LLM_FAILURE_REPLY
        except Exception:  # noqa: BLE001 — бот не должен молчать на неожиданный сбой
            logger.exception("Неожиданный сбой при обращении к LLM")
            reply = LLM_FAILURE_REPLY
        reply = _truncate(reply)

    _persist_turn(conn, session_id, user_text, reply, ts)
    return reply


def render_plan(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Текст ``/plan``: готовые узлы маршрута с режимом прохода."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return "Граф курса пуст. Загрузи seed: python -m llm_tutor.course.seed"

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
    rag_top_k: int = DEFAULT_RAG_TOP_K,
    dialog_tail: int = DEFAULT_DIALOG_TAIL,
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
        await message.answer(render_plan(conn))

    # Обработчик свободного текста: не трогает команды (иначе CommandStop был
    # бы перехвачен) и не лезет в незавершённые FSM-потоки (анкета, диагностика) —
    # их шаги обрабатывают свои роутеры.
    @router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        reply = await handle_message(
            conn,
            client,
            model,
            message.text or "",
            rag_top_k=rag_top_k,
            dialog_tail=dialog_tail,
        )
        await message.answer(reply)

    return router
