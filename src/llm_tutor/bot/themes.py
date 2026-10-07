"""Навигация по узлам темы: список со статусами и переход к выбранному.

Прыжок вперёд (не закрыты жёсткие пререквизиты) требует подтверждения —
это предупреждение, а не запрет: ученик решает сам.
"""

import sqlite3
import time
from typing import Literal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from llm_tutor.bot.render import PARSE_MODE, escape
from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, route as route_mod
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY

NodeStatus = Literal["closed", "current", "available", "ahead"]

STATUS_ICONS: dict[str, str] = {
    "closed": "✅",
    "current": "▶️",
    "available": "🟢",
    "ahead": "🔜",
}

THEMES_PROMPT = (
    "🎚 <b>Куда идём?</b>\n\n"
    "Можно вернуться к пройденному или заглянуть вперёд — "
    "про будущее предупрежу, там ещё не закрыты пререквизиты."
)


def _is_closed(
    conn: sqlite3.Connection, node_id: str, *, now: float | None, settings: Settings
) -> bool:
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
    if _is_closed(conn, node_id, now=now, settings=settings):
        return "closed"
    prereqs = graph.hard_prerequisites(node_id)
    if all(_is_closed(conn, prereq, now=now, settings=settings) for prereq in prereqs):
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

    new_state = state.model_copy(
        update={
            "current_node_id": node_id,
            "phase": "explain",
            "mode": None,
            "node_streak": 0,
            "hint_level": 0,
            "pending_item_id": None,
            "last_activity": stamp,
        }
    )
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    text = (
        f"Ок, тема — <b>{escape(name)}</b>. "
        "Спроси, что непонятно, или жми 🎯 Задание."
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

    def _ahead_prereq_names(graph: CourseGraph, node_id: str) -> list[str]:
        return [
            graph.concept(p).name
            for p in graph.hard_prerequisites(node_id)
            if not _is_closed(conn, p, now=time.time(), settings=settings)
        ]

    @router.callback_query(F.data.startswith("theme_go:"))
    async def on_theme_go(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        text = switch_node(conn, node_id, settings=settings)
        await callback.message.answer(text, parse_mode=PARSE_MODE)
        await callback.answer()

    @router.callback_query(F.data.startswith("theme:"))
    async def on_theme(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        graph = CourseGraph.load(conn)
        # Это чтение, а не ход: сессию НЕ заводим (как в ``_pending_item``).
        session_id = repos.get_open_session(conn)
        state = repos.get_session_state(conn, session_id) if session_id else SessionState()
        now = time.time()
        status = node_status(conn, graph, node_id, state, now=now, settings=settings)
        if status == "ahead":
            prereqs = ", ".join(_ahead_prereq_names(graph, node_id))
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
        await callback.answer()

    @router.callback_query(F.data == "theme_cancel")
    async def on_theme_cancel(callback: CallbackQuery) -> None:
        await callback.answer("Остаёмся на месте")

    return router
