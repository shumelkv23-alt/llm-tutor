"""Слой представления бота: экранирование и тексты сообщений.

Всё, что бот отправляет с ``parse_mode=HTML``, формируется здесь. Тексты
модели и данные из БД перед вставкой экранируются (``escape``) — модель не
должна уметь вставлять разметку в свой ответ.
"""

import html
import sqlite3

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY
from llm_tutor.schemas import SessionState
from llm_tutor.student import route as route_mod
from llm_tutor.student.planner import MODE_LABELS

PARSE_MODE = "HTML"
# Лимит Telegram 4096; держим запас.
MAX_MESSAGE = 4000


def escape(text: str) -> str:
    """Экранирует динамический текст для ``parse_mode=HTML``."""
    return html.escape(text, quote=False)


def fit(text: str) -> str:
    """Обрезает уже экранированный текст до лимита, не разрывая сущность.

    Экранировать нужно ДО обрезки: сущности длиннее исходных символов, и
    обрезка по «сырому» тексту пробила бы лимит Telegram. Но и резать по
    экранированному нельзя вслепую — можно оборвать ``&amp;`` на половине.
    """
    if len(text) <= MAX_MESSAGE:
        return text
    cut = text[:MAX_MESSAGE]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut + "…"


def _first_state(conn: sqlite3.Connection) -> SessionState:
    """Состояние открытой сессии (или пустое, если сессии ещё нет)."""
    session_id = repos.get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id else SessionState()


def render_plan(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Текст ``/plan``: маршрут к цели с прогрессом (перенос из handlers)."""
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return EMPTY_GRAPH_REPLY

    session_state = state or _first_state(conn)
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=session_state.current_node_id,
        previous=session_state.route,
        now=now,
        settings=settings,
    )
    if not route.steps:
        return EMPTY_GRAPH_REPLY
    if route.closed_count == len(route.steps):
        return "Всё доступное уже освоено — можно двигаться дальше или взять цель посложнее."

    goal_name = (
        graph.concept(route.goal_concept_id).name
        if route.goal_concept_id is not None
        else "вершина темы"
    )
    lines = [
        f"Маршрут: закрыто {route.closed_count} из {len(route.steps)}. Цель — {goal_name}."
    ]
    current = next((step for step in route.steps if step.status == "current"), None)
    if current is not None:
        lines.append(
            f"Сейчас: {graph.concept(current.concept_id).name} "
            f"({MODE_LABELS[current.mode]})."
        )
    ahead = [step for step in route.steps if step.status == "ahead"][:5]
    if ahead:
        names = ", ".join(
            f"{graph.concept(step.concept_id).name} ({MODE_LABELS[step.mode]})"
            for step in ahead
        )
        lines.append(f"Дальше: {names}.")
    return "\n".join(lines)
