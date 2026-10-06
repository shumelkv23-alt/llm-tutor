"""Маршрут ученика: путь к цели, снимок состояния узлов и пересмотр (Срез 9).

Маршрут — это не список «что доступно сейчас», а путь до цели: предки цели
плюс сама цель (архитектура, §6.2). Снимок живёт в состоянии сессии, поэтому
видно, что изменилось, и можно не дёргать план по мелочам (§6.4).
"""

import sqlite3
import time
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep, SessionState
from llm_tutor.student import planner
from llm_tutor.student.survey import GOAL_CONCEPT_KEY


@dataclass(frozen=True)
class RouteChanges:
    """Что изменилось между снимком маршрута и новым расчётом."""

    closed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    current_changed: bool


def _scope(graph: CourseGraph, goal_concept_id: str | None) -> list[str]:
    """Узлы маршрута в топопорядке: предки цели и сама цель."""
    if goal_concept_id is None:
        return graph.topo_order()
    graph.concept(goal_concept_id)  # падает, если цели нет в графе
    in_goal = graph.ancestors(goal_concept_id) | {goal_concept_id}
    return [node_id for node_id in graph.topo_order() if node_id in in_goal]


def _current_of(route: Route | None) -> str | None:
    """Узел, помеченный в снимке текущим."""
    if route is None:
        return None
    for step in route.steps:
        if step.status == "current":
            return step.concept_id
    return None


def build_route(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    *,
    goal_concept_id: str | None = None,
    current_node_id: str | None = None,
    previous: Route | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> Route:
    """Строит маршрут; узлы, закрытые раньше, сохраняют свой статус."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    closed_before = {
        step.concept_id
        for step in (previous.steps if previous is not None else [])
        if step.status == "closed"
    }

    steps: list[RouteStep] = []
    for concept_id in _scope(graph, goal_concept_id):
        mode = planner.mode_for_node(conn, graph, concept_id, now=stamp, settings=s)
        if concept_id in closed_before or mode == "skip":
            status = "closed"
        elif concept_id == current_node_id:
            status = "current"
        else:
            status = "ahead"
        steps.append(RouteStep(concept_id=concept_id, mode=mode, status=status))
    return Route(goal_concept_id=goal_concept_id, steps=steps, built_at=stamp)


def diff_routes(previous: Route | None, current: Route) -> RouteChanges:
    """Сравнивает снимок с новым расчётом."""
    old = {step.concept_id: step for step in (previous.steps if previous is not None else [])}
    new = {step.concept_id: step for step in current.steps}
    return RouteChanges(
        closed=tuple(
            node_id
            for node_id, step in new.items()
            if step.status == "closed"
            and node_id in old
            and old[node_id].status != "closed"
        ),
        added=tuple(node_id for node_id in new if node_id not in old),
        removed=tuple(node_id for node_id in old if node_id not in new),
        current_changed=_current_of(previous) != _current_of(current),
    )


def is_significant(changes: RouteChanges, *, min_steps: int = 2) -> bool:
    """Стоит ли вообще говорить ученику о пересмотре (§6.4 — «малые различия»)."""
    return (
        bool(changes.closed)
        or changes.current_changed
        or len(changes.added) + len(changes.removed) >= min_steps
    )


def format_route_change(changes: RouteChanges) -> str:
    """Строка об изменении маршрута для ответа ученику."""
    parts = []
    if changes.closed:
        parts.append("закрыт " + ", ".join(changes.closed))
    if changes.added:
        parts.append("добавилось " + ", ".join(changes.added))
    if changes.removed:
        parts.append("ушло " + ", ".join(changes.removed))
    return "Маршрут перестроен: " + "; ".join(parts) + "."


def goal_concept_id(conn: sqlite3.Connection) -> str | None:
    """Цель из анкеты (``None``, если ученик её не выбирал)."""
    return repos.get_fact(conn, GOAL_CONCEPT_KEY)


def goal_for(conn: sqlite3.Connection, graph: CourseGraph) -> str | None:
    """Цель из анкеты, если такой узел есть в графе (seed мог поменяться)."""
    goal = goal_concept_id(conn)
    return goal if goal is not None and goal in graph.node_ids else None


def refresh(
    conn: sqlite3.Connection,
    state: SessionState,
    graph: CourseGraph,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> tuple[Route, str | None]:
    """Пересчитывает маршрут; отдаёт снимок и текст о значимом изменении."""
    s = settings or get_settings()
    fresh = build_route(
        conn,
        graph,
        goal_concept_id=goal_for(conn, graph),
        current_node_id=state.current_node_id,
        previous=state.route,
        now=now,
        settings=s,
    )
    if state.route is None:
        # Первый расчёт — это не «изменение»: сообщать не о чем.
        return fresh, None

    changes = diff_routes(state.route, fresh)
    note = (
        format_route_change(changes)
        if is_significant(changes, min_steps=s.route_min_significant_changes)
        else None
    )
    return fresh, note
