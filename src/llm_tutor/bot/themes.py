"""Навигация по узлам темы: список со статусами и переход к выбранному.

Прыжок вперёд (не закрыты жёсткие пререквизиты) требует подтверждения —
это предупреждение, а не запрет: ученик решает сам.
"""

import logging
import sqlite3
import time
from typing import Literal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from llm_tutor.bot.render import PARSE_MODE, escape, fit
from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import BOT_FAILURE_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, route as route_mod
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY

logger = logging.getLogger(__name__)

NodeStatus = Literal["closed", "current", "claimed", "available", "ahead"]

STATUS_ICONS: dict[str, str] = {
    "closed": "✅",
    "current": "▶️",
    "claimed": "🔍",
    "available": "🟢",
    "ahead": "🔜",
}

THEMES_PROMPT = (
    "🎚 <b>Куда идём?</b>\n\n"
    "Можно вернуться к пройденному или заглянуть вперёд — "
    "про будущее предупрежу, там ещё не закрыты пререквизиты."
)


def _closed_from_route(state: SessionState) -> set[str]:
    """Узлы, закрытые в снимке маршрута сессии — запись тьютора о закрытии.

    Ученик закрывает узел серией чистых ответов ИЛИ уверенным владением;
    снимок маршрута хранит оба случая, поэтому он — источник правды.
    """
    if state.route is None:
        return set()
    return {step.concept_id for step in state.route.steps if step.status == "closed"}


def _claimed_from_route(state: SessionState) -> set[str]:
    """Узлы, заявленные в анкете и ещё не подтверждённые (снимок маршрута)."""
    if state.route is None:
        return set()
    return {step.concept_id for step in state.route.steps if step.status == "claimed"}


def _is_closed(
    conn: sqlite3.Connection,
    node_id: str,
    state: SessionState,
    *,
    now: float | None,
    settings: Settings,
) -> bool:
    if node_id in _closed_from_route(state):
        return True
    mastery = beta.estimate(conn, node_id, now=now, settings=settings)
    return (
        mastery.mean >= settings.mastery_skip_threshold
        and mastery.uncertainty <= CONFIDENT_UNCERTAINTY
    )


def node_status(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    node_id: str,
    state: SessionState,
    *,
    now: float | None,
    settings: Settings,
) -> NodeStatus:
    """Статус узла для списка тем."""
    if node_id == state.current_node_id:
        return "current"
    if _is_closed(conn, node_id, state, now=now, settings=settings):
        return "closed"
    claimed = _claimed_from_route(state)
    if node_id in claimed:
        return "claimed"
    prereqs = graph.hard_prerequisites(node_id)
    if all(
        prereq in claimed or _is_closed(conn, prereq, state, now=now, settings=settings)
        for prereq in prereqs
    ):
        return "available"
    return "ahead"


def themes_keyboard(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> InlineKeyboardMarkup:
    """Инлайн-список всех узлов темы со статусами (по 2 в ряду)."""
    s = settings or get_settings()
    graph = CourseGraph.load(conn)
    # Это чтение, а не ход: сессию НЕ заводим (как в ``_pending_item``).
    session_id = repos.get_open_session(conn)
    session_state = state or (
        repos.get_session_state(conn, session_id) if session_id else SessionState()
    )
    buttons: list[InlineKeyboardButton] = []
    for node_id in graph.topo_order():
        status = node_status(conn, graph, node_id, session_state, now=now, settings=s)
        label = f"{STATUS_ICONS[status]} {graph.concept(node_id).name}"
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"theme:{node_id}")
        )
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def switch_node(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Переход к узлу: смена текущей темы, снятие висящего задания."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    name = graph.concept(node_id).name

    # Повторный выбор той же темы (ученик вернулся в список и ткнул в текущую)
    # не должен молча стирать набранную серию, лестницу подсказок и висящее
    # задание: тема та же, прогресс по ней никуда не делся.
    if state.current_node_id == node_id:
        keep = {
            "phase": state.phase,
            "node_streak": state.node_streak,
            "hint_level": state.hint_level,
            "pending_item_id": state.pending_item_id,
            "lesson_item_ids": state.lesson_item_ids,
        }
    else:
        keep = {
            "phase": "explain",
            "node_streak": 0,
            "hint_level": 0,
            "pending_item_id": None,
            # Список выданного в уроке — про узел: на новой теме он начинает
            # заход заново (спека §5.3).
            "lesson_item_ids": [],
        }
    new_state = state.model_copy(
        update={
            "current_node_id": node_id,
            "mode": None,
            # Переход на другую тему снимает проверочный проход (спека §5.2).
            "verify_item_ids": [],
            "last_activity": stamp,
            **keep,
        }
    )
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    text = (
        f"Ок, тема — <b>{escape(name)}</b>. "
        "Спроси, что непонятно, — объясню."
    )
    post_turn(
        conn,
        session_id,
        user_text=f"Перейти к теме: {node_id}",
        assistant_text=text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=stamp,
    )
    return text


def make_themes_router(conn: sqlite3.Connection, settings: Settings) -> Router:
    """Роутер выбора темы: узел сразу либо с подтверждением для «вперёд»."""
    router = Router()

    def _ahead_prereq_names(
        graph: CourseGraph, node_id: str, state: SessionState
    ) -> list[str]:
        return [
            graph.concept(p).name
            for p in graph.hard_prerequisites(node_id)
            if not _is_closed(conn, p, state, now=time.time(), settings=settings)
        ]

    @router.callback_query(F.data.startswith("theme_go:"))
    async def on_theme_go(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        # Данные колбэка подконтрольны клиенту: неизвестный узел не должен
        # ронять хендлер до ответа на нажатие (иначе «часик» виснет).
        try:
            if not CourseGraph.load(conn).has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            text = switch_node(conn, node_id, settings=settings)
            await callback.message.answer(text, parse_mode=PARSE_MODE)
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой перехода к теме: %s", node_id)
            await callback.message.answer(
                fit(escape(BOT_FAILURE_REPLY)), parse_mode=PARSE_MODE
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("theme:"))
    async def on_theme(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        try:
            graph = CourseGraph.load(conn)
            # Неизвестный узел (подделка колбэка) — отвечаем и выходим.
            if not graph.has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            # Это чтение, а не ход: сессию НЕ заводим (как в ``_pending_item``).
            session_id = repos.get_open_session(conn)
            state = (
                repos.get_session_state(conn, session_id) if session_id else SessionState()
            )
            now = time.time()
            status = node_status(conn, graph, node_id, state, now=now, settings=settings)
            if status == "ahead":
                prereqs = ", ".join(_ahead_prereq_names(graph, node_id, state))
                text = (
                    f"«{escape(graph.concept(node_id).name)}» — не закрыты "
                    f"пререквизиты ({escape(prereqs)}). Всё равно идём?"
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🎯 Да", callback_data=f"theme_go:{node_id}"),
                            InlineKeyboardButton(text="↩️ Нет", callback_data="theme_cancel"),
                        ]
                    ]
                )
                await callback.message.answer(text, parse_mode=PARSE_MODE, reply_markup=keyboard)
            else:
                text = switch_node(conn, node_id, settings=settings)
                await callback.message.answer(text, parse_mode=PARSE_MODE)
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой выбора темы: %s", node_id)
            await callback.message.answer(
                fit(escape(BOT_FAILURE_REPLY)), parse_mode=PARSE_MODE
            )
        await callback.answer()

    @router.callback_query(F.data == "theme_cancel")
    async def on_theme_cancel(callback: CallbackQuery) -> None:
        await callback.answer("Остаёмся на месте")

    return router
