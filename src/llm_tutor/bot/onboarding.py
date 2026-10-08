"""Онбординг: представление → анкета → согласование маршрута (Срез 18).

Экран согласования даёт ученику сказать «это я уже знаю» про конкретный узел.
Заявление пишет СЛАБОЕ свидетельство (``student/self_report``) и пересчитывает
маршрут; закрыть узел оно не может — закрытие только через проверку.
"""

import logging
import sqlite3
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from llm_tutor.bot import menu, render
from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import BOT_FAILURE_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import route as route_mod
from llm_tutor.student import self_report

logger = logging.getLogger(__name__)

ROUTE_OK_LABEL = "✅ Меня всё устраивает"
ROUTE_ALL_THEMES_LABEL = "🎚 Все темы"
ROUTE_HINT_REPLY = "Выбери узел кнопкой или жми ✅ Меня всё устраивает."
ROUTE_FIXED_TEMPLATE = (
    "✅ Маршрут зафиксирован: {total} шагов, цель — «{goal}».\n\n"
    "Жми ▶️ — начнём с темы «{first}»."
)
ROUTE_FIXED_EMPTY = "✅ Маршрут зафиксирован. Жми ▶️ Продолжить обучение."


class OnboardingFlow(StatesGroup):
    """Согласование маршрута: ждём нажатия кнопки узла или подтверждения."""

    route_review = State()


def _node_keyboard(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> InlineKeyboardMarkup:
    """Кнопки узлов окна маршрута + полный список + подтверждение."""
    graph = CourseGraph.load(conn)
    buttons = [
        InlineKeyboardButton(
            text=graph.concept(step.concept_id).name,
            callback_data=f"route:node:{step.concept_id}",
        )
        for step in render.route_window(conn, now=now, settings=settings)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=ROUTE_ALL_THEMES_LABEL, callback_data="menu:themes")])
    rows.append([InlineKeyboardButton(text=ROUTE_OK_LABEL, callback_data="route:ok")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _node_actions_keyboard(node_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я это знаю", callback_data=f"route:know:{node_id}")],
            [
                InlineKeyboardButton(
                    text="❗ Я это не знаю", callback_data=f"route:unknown:{node_id}"
                )
            ],
            [InlineKeyboardButton(text="↩️ Оставить как есть", callback_data="route:leave")],
        ]
    )


async def show_route_screen(
    message: Message,
    state: FSMContext,
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    note: str | None = None,
) -> None:
    """Показывает экран согласования и переводит FSM в ожидание выбора."""
    await state.set_state(OnboardingFlow.route_review)
    text = render.render_route_screen(conn, settings=settings)
    if note:
        text = f"{render.escape(note)}\n\n{text}"
    await message.answer(
        text,
        parse_mode=render.PARSE_MODE,
        reply_markup=_node_keyboard(conn, settings=settings),
    )


def make_onboarding_router(conn: sqlite3.Connection, settings: Settings) -> Router:
    """Роутер согласования маршрута."""
    router = Router()

    def _record(node_id: str, *, correct: bool) -> None:
        """Слабое свидетельство + пересчёт маршрута одним коммитом.

        Свидетельство и снимок маршрута пишутся общей транзакцией: сбой
        посередине не оставит начисленную самооценку без пересчёта (повторное
        нажатие начислило бы её второй раз).
        """
        stamp = time.time()
        session_id = repos.ensure_open_session(conn, stamp)
        state = repos.get_session_state(conn, session_id)
        graph = CourseGraph.load(conn)
        try:
            self_report.apply(
                conn, node_id, correct=correct, now=stamp, settings=settings, commit=False
            )
            fresh_route, _ = route_mod.refresh(conn, state, graph, now=stamp, settings=settings)
            repos.update_session_state(
                conn,
                session_id,
                state.model_copy(update={"route": fresh_route}),
                commit=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @router.callback_query(OnboardingFlow.route_review, F.data.startswith("route:node:"))
    async def on_route_node(callback: CallbackQuery) -> None:
        node_id = (callback.data or "").split(":", 2)[2]
        # Данные колбэка подконтрольны клиенту: неизвестный узел не должен
        # ронять хендлер (иначе у ученика зависает «часик»).
        if not CourseGraph.load(conn).has_node(node_id):
            await callback.answer("Тема недоступна")
            return
        await callback.message.answer(
            "Что с этой темой?", reply_markup=_node_actions_keyboard(node_id)
        )
        await callback.answer()

    @router.callback_query(OnboardingFlow.route_review, F.data.startswith("route:know:"))
    async def on_route_know(callback: CallbackQuery, state: FSMContext) -> None:
        node_id = (callback.data or "").split(":", 2)[2]
        try:
            if not CourseGraph.load(conn).has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            _record(node_id, correct=True)
            await callback.answer()
            await show_route_screen(
                callback.message, state, conn, settings, note="Записал: знаешь эту тему."
            )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой правки маршрута: %s", node_id)
            await callback.message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            await callback.answer()

    @router.callback_query(OnboardingFlow.route_review, F.data.startswith("route:unknown:"))
    async def on_route_unknown(callback: CallbackQuery, state: FSMContext) -> None:
        node_id = (callback.data or "").split(":", 2)[2]
        try:
            if not CourseGraph.load(conn).has_node(node_id):
                await callback.answer("Тема недоступна")
                return
            _record(node_id, correct=False)
            await callback.answer()
            await show_route_screen(
                callback.message, state, conn, settings, note="Записал: тема новая."
            )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой правки маршрута: %s", node_id)
            await callback.message.answer(
                render.fit(render.escape(BOT_FAILURE_REPLY)), parse_mode=render.PARSE_MODE
            )
            await callback.answer()

    @router.callback_query(OnboardingFlow.route_review, F.data == "route:leave")
    async def on_route_leave(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await show_route_screen(callback.message, state, conn, settings)

    @router.callback_query(OnboardingFlow.route_review, F.data == "route:ok")
    async def on_route_ok(callback: CallbackQuery, state: FSMContext) -> None:
        try:
            graph = CourseGraph.load(conn)
            session_id = repos.get_open_session(conn)
            current = (
                repos.get_session_state(conn, session_id) if session_id else SessionState()
            )
            route = current.route or route_mod.build_route(
                conn, graph, goal_concept_id=route_mod.goal_for(conn, graph), settings=settings
            )
            first = next(
                (step.concept_id for step in route.steps if step.status != "closed"), None
            )
            if first is None:
                text = ROUTE_FIXED_EMPTY
            else:
                goal_id = route.goal_concept_id
                text = ROUTE_FIXED_TEMPLATE.format(
                    total=len(route.steps),
                    goal=graph.concept(goal_id).name if goal_id else "вершина темы",
                    first=graph.concept(first).name,
                )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой фиксации маршрута")
            text = BOT_FAILURE_REPLY
        await state.clear()
        await callback.message.answer(
            render.escape(text),
            parse_mode=render.PARSE_MODE,
            reply_markup=menu.resume_keyboard(),
        )
        await callback.answer()

    # Команды пропускаем дальше: иначе экран согласования проглотил бы /start
    # и ученик остался бы в этом состоянии без выхода (роутер идёт раньше
    # основного, а его хендлеры команд не знают про FSM-состояние).
    @router.message(OnboardingFlow.route_review, F.text & ~F.text.startswith("/"))
    async def on_route_text(message: Message) -> None:
        # Экран согласования принимает только кнопки: иначе реплика ушла бы в
        # тьюторский ход и узел выбрался бы «на слух».
        await message.answer(ROUTE_HINT_REPLY)

    return router
