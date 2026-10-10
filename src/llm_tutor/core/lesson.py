"""Занятие вне конкретного интерфейса: статусы тем курса.

Логика переехала из ``bot/themes.py``: она нужна и боту, и вебу, а слой
``core`` не знает ни про Telegram, ни про браузер.
"""

import sqlite3
from typing import Literal

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY

NodeStatus = Literal["closed", "current", "claimed", "available", "ahead"]


def closed_from_route(state: SessionState) -> set[str]:
    """Узлы, закрытые в снимке маршрута сессии — запись тьютора о закрытии.

    Ученик закрывает узел серией чистых ответов ИЛИ уверенным владением;
    снимок маршрута хранит оба случая, поэтому он — источник правды.
    """
    if state.route is None:
        return set()
    return {step.concept_id for step in state.route.steps if step.status == "closed"}


def claimed_from_route(state: SessionState) -> set[str]:
    """Узлы, заявленные в анкете и ещё не подтверждённые (снимок маршрута)."""
    if state.route is None:
        return set()
    return {step.concept_id for step in state.route.steps if step.status == "claimed"}


def is_closed(
    conn: sqlite3.Connection,
    node_id: str,
    state: SessionState,
    *,
    now: float | None,
    settings: Settings,
) -> bool:
    """Закрыт ли узел: по снимку маршрута или уверенным владением."""
    if node_id in closed_from_route(state):
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
    if is_closed(conn, node_id, state, now=now, settings=settings):
        return "closed"
    claimed = claimed_from_route(state)
    if node_id in claimed:
        return "claimed"
    prereqs = graph.hard_prerequisites(node_id)
    if all(
        prereq in claimed or is_closed(conn, prereq, state, now=now, settings=settings)
        for prereq in prereqs
    ):
        return "available"
    return "ahead"
