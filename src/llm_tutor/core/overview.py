"""Сводки по курсу ученика для экранов (структуры, а не текст).

Чтение, а не ход: сессию не заводим, журнал не трогаем.
"""

import sqlite3
from dataclasses import dataclass

from llm_tutor.config import Settings
from llm_tutor.core.lesson import NodeStatus, is_closed, node_status
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import RouteStep, SessionState
from llm_tutor.student import beta, survey
from llm_tutor.student import route as route_mod
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY


@dataclass(frozen=True)
class CourseProgress:
    total: int
    closed: int
    survey_done: bool
    current_name: str | None


def _state(conn: sqlite3.Connection) -> SessionState:
    session_id = repos.get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id else SessionState()


def course_progress(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings
) -> CourseProgress:
    """Сколько тем закрыто, пройдена ли анкета и какая тема текущая."""
    graph = CourseGraph.load(conn)
    state = _state(conn)
    nodes = graph.topo_order()
    closed = sum(
        1 for node_id in nodes if is_closed(conn, node_id, state, now=now, settings=settings)
    )
    current = state.current_node_id
    return CourseProgress(
        total=len(nodes),
        closed=closed,
        survey_done=survey.is_completed(conn),
        current_name=graph.concept(current).name if current and graph.has_node(current) else None,
    )


@dataclass(frozen=True)
class TopicRow:
    """Тема курса для списка и профиля."""

    id: str
    name: str
    description: str | None
    status: NodeStatus
    mastery: float
    uncertainty: float

    @property
    def confident(self) -> bool:
        """Оценке владения можно верить (данных достаточно)."""
        return self.uncertainty <= CONFIDENT_UNCERTAINTY


def topic_rows(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings
) -> list[TopicRow]:
    """Все темы курса в порядке изучения: статус и оценка владения."""
    graph = CourseGraph.load(conn)
    state = _state(conn)
    rows: list[TopicRow] = []
    for node_id in graph.topo_order():
        concept = graph.concept(node_id)
        estimate = beta.estimate(conn, node_id, now=now, settings=settings)
        rows.append(
            TopicRow(
                id=node_id,
                name=concept.name,
                description=concept.description,
                status=node_status(conn, graph, node_id, state, now=now, settings=settings),
                mastery=estimate.mean,
                uncertainty=estimate.uncertainty,
            )
        )
    return rows


@dataclass(frozen=True)
class RouteOverview:
    steps: list[RouteStep]
    closed: int
    total: int


def route_overview(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None,
) -> RouteOverview:
    """Маршрут к цели целиком (снимок пересчитывается, но не сохраняется)."""
    graph = CourseGraph.load(conn)
    session_state = state or _state(conn)
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=session_state.current_node_id,
        previous=session_state.route,
        now=now,
        settings=settings,
    )
    return RouteOverview(steps=route.steps, closed=route.closed_count, total=len(route.steps))
