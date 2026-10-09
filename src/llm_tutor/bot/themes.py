"""Навигация по курсу: модули, внутри — темы со статусами (срез 27).

Прыжок вперёд (не закрыты жёсткие пререквизиты) требует подтверждения —
это предупреждение, а не запрет: ученик решает сам.
"""

import logging
import sqlite3
import time
from typing import Literal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from llm_tutor.bot.render import PARSE_MODE, escape, fit
from llm_tutor.bot.survey import safe_edit, start_survey
from llm_tutor.config import Settings, get_settings
from llm_tutor.core.turn import post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import BOT_FAILURE_REPLY
from llm_tutor.schemas import SessionState, Topic
from llm_tutor.student import beta, survey
from llm_tutor.student import route as route_mod
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
    "Выбери модуль — внутри видно, что пройдено и что впереди."
)
MODULE_PROMPT = (
    "Можно вернуться к пройденному или заглянуть вперёд — "
    "про будущее предупрежу, там ещё не закрыты пререквизиты."
)
MODULE_ICONS: dict[str, str] = {"done": "✅", "current": "▶️", "ahead": "🔜"}
TOPICS_DATA = "topics"
TOPICS_BACK_LABEL = "← Модули"


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


def _read_state(conn: sqlite3.Connection, state: SessionState | None) -> SessionState:
    """Состояние для меню — чтение: сессию НЕ заводим (как в ``_pending_item``)."""
    if state is not None:
        return state
    session_id = repos.get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id else SessionState()


def topics_keyboard(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> InlineKeyboardMarkup:
    """Список модулей со статусами: пройден, текущий, впереди."""
    s = settings or get_settings()
    graph = CourseGraph.load(conn)
    session_state = _read_state(conn, state)
    current = route_mod.topic_for(graph, session_state)
    rows = []
    for topic in repos.get_topics(conn):
        nodes = graph.topic_nodes(topic.number)
        if nodes and all(
            _is_closed(conn, node, session_state, now=now, settings=s) for node in nodes
        ):
            status = "done"
        elif topic.number == current:
            status = "current"
        else:
            status = "ahead"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{MODULE_ICONS[status]} {topic.number}. {topic.title}",
                    callback_data=f"topic:{topic.number}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def themes_keyboard(
    conn: sqlite3.Connection,
    topic_id: int,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> InlineKeyboardMarkup:
    """Инлайн-список тем модуля со статусами (по 2 в ряду) и «← Модули»."""
    s = settings or get_settings()
    graph = CourseGraph.load(conn)
    session_state = _read_state(conn, state)
    buttons: list[InlineKeyboardButton] = []
    for node_id in graph.topic_nodes(topic_id):
        status = node_status(conn, graph, node_id, session_state, now=now, settings=s)
        label = f"{STATUS_ICONS[status]} {graph.concept(node_id).name}"
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"theme:{node_id}")
        )
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=TOPICS_BACK_LABEL, callback_data=TOPICS_DATA)])
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
            # Заявленный в анкете пререквизит статус уже засчитал.
            if p not in _claimed_from_route(state)
            and not _is_closed(conn, p, state, now=time.time(), settings=settings)
        ]

    def _survey_first(graph: CourseGraph, node_id: str) -> Topic | None:
        """Модуль темы, анкета которого не пройдена (§5.2), иначе ``None``."""
        topic = repos.get_topic(conn, graph.topic_of(node_id))
        if topic is None or survey.is_completed(conn, topic.survey):
            return None
        return topic

    def _enter_module(graph: CourseGraph, topic_id: int) -> None:
        """Прыжок в модуль: он становится модулем работы ещё до анкеты.

        Иначе /start посреди анкеты или рестарт бота (FSM пуст) решали бы
        «модуль работы» по старому снимку и открыли бы анкету модуля, откуда
        ученик ушёл (ревью среза 26, 26-2). Текущая тема и висящее задание
        прошлого модуля снимаются — как при обычном переходе к теме.
        """
        stamp = time.time()
        session_id = repos.ensure_open_session(conn, stamp)
        state = repos.get_session_state(conn, session_id)
        route = state.route or route_mod.build_route(
            conn,
            graph,
            goal_concept_id=route_mod.goal_for(conn, graph),
            now=stamp,
            settings=settings,
        )
        repos.update_session_state(
            conn,
            session_id,
            state.model_copy(
                update={
                    "current_node_id": None,
                    "pending_item_id": None,
                    "mode": None,
                    "phase": "explain",
                    "node_streak": 0,
                    "verify_item_ids": [],
                    "lesson_item_ids": [],
                    "route": route_mod.release_current(route).model_copy(
                        update={"topic_id": topic_id}
                    ),
                    "last_activity": stamp,
                }
            ),
        )

    async def _go(callback: CallbackQuery, state: FSMContext | None, node_id: str) -> None:
        """Переход к теме; в модуль с непройденной анкетой — через анкету."""
        graph = CourseGraph.load(conn)
        topic = _survey_first(graph, node_id)
        if topic is not None and state is not None:
            _enter_module(graph, topic.number)
            await start_survey(callback.message, state, topic, then_node=node_id)
            return
        text = switch_node(conn, node_id, settings=settings)
        await callback.message.answer(text, parse_mode=PARSE_MODE)

    @router.callback_query(F.data.startswith("topic:"))
    async def on_topic(callback: CallbackQuery) -> None:
        raw = (callback.data or "").split(":", 1)[1]
        try:
            # isdigit пропускает «²», а длинное число роняет SQLite — адресное
            # «Модуль недоступен» вместо общей ошибки (ревью среза 27, 27-3).
            valid = raw.isascii() and raw.isdecimal() and len(raw) <= 3
            topic = repos.get_topic(conn, int(raw)) if valid else None
            if topic is None:
                await callback.answer("Модуль недоступен")
                return
            text = f"📘 <b>Модуль {topic.number} · {escape(topic.title)}</b>\n\n{MODULE_PROMPT}"
            await safe_edit(
                callback.message, text, themes_keyboard(conn, topic.number, settings=settings)
            )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой списка тем модуля: %s", raw)
            await callback.message.answer(fit(escape(BOT_FAILURE_REPLY)), parse_mode=PARSE_MODE)
        await callback.answer()

    @router.callback_query(F.data == TOPICS_DATA)
    async def on_topics(callback: CallbackQuery) -> None:
        try:
            await safe_edit(
                callback.message, THEMES_PROMPT, topics_keyboard(conn, settings=settings)
            )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой списка модулей")
            await callback.message.answer(fit(escape(BOT_FAILURE_REPLY)), parse_mode=PARSE_MODE)
        await callback.answer()

    @router.callback_query(F.data.startswith("theme_go:"))
    async def on_theme_go(callback: CallbackQuery, state: FSMContext | None = None) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        # Данные колбэка подконтрольны клиенту: неизвестный узел не должен
        # ронять хендлер до ответа на нажатие (иначе «часик» виснет).
        try:
            if not CourseGraph.load(conn).has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            await _go(callback, state, node_id)
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой перехода к теме: %s", node_id)
            await callback.message.answer(
                fit(escape(BOT_FAILURE_REPLY)), parse_mode=PARSE_MODE
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("theme:"))
    async def on_theme(callback: CallbackQuery, state: FSMContext | None = None) -> None:
        node_id = (callback.data or "").split(":", 1)[1]
        try:
            graph = CourseGraph.load(conn)
            # Неизвестный узел (подделка колбэка) — отвечаем и выходим.
            if not graph.has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            # Это чтение, а не ход: сессию НЕ заводим (как в ``_pending_item``).
            session_id = repos.get_open_session(conn)
            session_state = (
                repos.get_session_state(conn, session_id) if session_id else SessionState()
            )
            now = time.time()
            status = node_status(conn, graph, node_id, session_state, now=now, settings=settings)
            if status == "ahead":
                prereqs = ", ".join(_ahead_prereq_names(graph, node_id, session_state))
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
                await _go(callback, state, node_id)
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
