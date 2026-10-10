"""Сводки по курсу ученика для экранов (структуры, а не текст).

Чтение, а не ход: сессию не заводим, журнал не трогаем.
"""

import sqlite3
from dataclasses import dataclass

from llm_tutor.config import Settings
from llm_tutor.core.lesson import is_closed
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState
from llm_tutor.student import survey


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
