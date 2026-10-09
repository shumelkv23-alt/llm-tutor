"""Маршрут ученика: путь к цели, снимок состояния узлов и пересмотр (Срез 9).

Маршрут — это не список «что доступно сейчас», а путь до цели: предки цели
плюс сама цель (архитектура, §6.2). Снимок живёт в состоянии сессии, поэтому
видно, что изменилось, и можно не дёргать план по мелочам (§6.4).
"""

import sqlite3
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep, SessionState
from llm_tutor.student import beta, planner
from llm_tutor.student.survey import GOAL_CONCEPT_KEY, claimed_concepts


@dataclass(frozen=True)
class RouteChanges:
    """Что изменилось между снимком маршрута и новым расчётом."""

    closed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    current_changed: bool


# Столько провалов ПОСЛЕ закрытия темы открывают её снова (§4.2 спеки модулей).
REOPEN_FAILURES = 2


def _failures_since(conn: sqlite3.Connection, concept_id: str, since: float) -> int:
    """Неудачные свидетельства по теме позже момента ``since``."""
    return sum(
        1
        for event in repos.get_events(conn, concept_id)
        if event.ts is not None and event.ts > since and event.result < 0.5
    )


def _completed_topics(graph: CourseGraph, previous: Route | None) -> list[int]:
    """Пройденные модули из прошлого снимка.

    Модуль, все темы которого в снимке закрыты, считается пройденным и без
    «🎉»: так снимок до среза 25, где topic01 закрыт целиком, при росте курса
    не празднует модуль 1 задним числом (аудит среза 25, H1).
    """
    if previous is None:
        return []
    done = list(previous.completed_topics)
    by_topic: dict[int, list[str]] = {}
    for step in previous.steps:
        if graph.has_node(step.concept_id):
            by_topic.setdefault(graph.topic_of(step.concept_id), []).append(step.status)
    for topic, statuses in sorted(by_topic.items()):
        if topic not in done and all(status == "closed" for status in statuses):
            done.append(topic)
    return done


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


def working_section(
    graph: CourseGraph, route: Route, topic_id: int | None
) -> list[str]:
    """Рабочий участок модуля (§4.1 спеки модулей), в порядке маршрута.

    Темы модуля плюс незакрытые темы прошлых модулей двух видов: предки тем
    модуля (на них он опирается) и темы, открывшиеся снова после закрытия
    (``closed_at`` у незакрытого шага — провал по заданию со вторичным весом
    или просроченное повторение, §4.2): забытое проходится здесь же.
    """
    if topic_id is None:
        return []
    known = [step for step in route.steps if graph.has_node(step.concept_id)]
    own = {step.concept_id for step in known if graph.topic_of(step.concept_id) == topic_id}
    earlier_open = [
        step
        for step in known
        if step.status != "closed" and graph.topic_of(step.concept_id) < topic_id
    ]
    reopened = {step.concept_id for step in earlier_open if step.closed_at is not None}
    # Предки и тем модуля, и открывшихся снова: иначе жёсткий пререквизит
    # открывшейся темы остался бы вне участка и запер его (аудит среза 25, M1).
    ancestors: set[str] = set()
    for node_id in own | reopened:
        ancestors |= graph.ancestors(node_id)
    pulled = reopened | {step.concept_id for step in earlier_open if step.concept_id in ancestors}
    return [step.concept_id for step in known if step.concept_id in own | pulled]


def topic_finished(graph: CourseGraph, route: Route, topic_id: int) -> bool:
    """Пройден ли модуль: все темы его рабочего участка закрыты."""
    status = {step.concept_id: step.status for step in route.steps}
    return all(status[node_id] == "closed" for node_id in working_section(graph, route, topic_id))


def choose_topic(
    graph: CourseGraph, route: Route, *, previous_topic: int | None = None
) -> int | None:
    """Модуль, над которым идёт работа (§4.1 спеки модулей).

    1. Текущая тема задаёт модуль — кроме незакрытого предка из прошлого
       модуля: его подтянул участок модуля, где идёт работа.
    2. Без текущей темы — прежний модуль, пока в его участке есть открытое.
    3. Иначе первый по номеру ещё не пройденный модуль с открытыми темами; если
       открытое осталось только в пройденных (повторение) — первый из них.
    """
    current = _current_of(route)
    if current is not None and graph.has_node(current):
        if previous_topic is not None and current in working_section(
            graph, route, previous_topic
        ):
            return previous_topic
        return graph.topic_of(current)
    if previous_topic is not None and not topic_finished(graph, route, previous_topic):
        return previous_topic
    open_topics = sorted(
        {
            graph.topic_of(step.concept_id)
            for step in route.steps
            if step.status != "closed" and graph.has_node(step.concept_id)
        }
    )
    fresh = [topic for topic in open_topics if topic not in route.completed_topics]
    if fresh:
        return fresh[0]
    return open_topics[0] if open_topics else None


def topic_for(graph: CourseGraph, state: SessionState) -> int | None:
    """Модуль занятия — для анкеты, промпта, RAG и экранов."""
    if state.route is not None:
        if state.route.topic_id is not None:
            return state.route.topic_id
        return choose_topic(graph, state.route)
    if state.current_node_id is not None and graph.has_node(state.current_node_id):
        return graph.topic_of(state.current_node_id)
    return graph.topic_ids[0] if graph.node_ids else None


def section_route(graph: CourseGraph, route: Route) -> Route:
    """Снимок, урезанный до рабочего участка модуля работы (экраны, промпт)."""
    topic = route.topic_id if route.topic_id is not None else choose_topic(graph, route)
    keep = set(working_section(graph, route, topic))
    return route.model_copy(
        update={
            "steps": [step for step in route.steps if step.concept_id in keep],
            "topic_id": topic,
        }
    )


def mark_claimed(route: Route, concept_ids: Collection[str]) -> Route:
    """Помечает заявленным то, что ещё впереди (анкета модуля, §4.1).

    Закрытое и текущее не трогаем: самооценка не отменяет доказанного.
    """
    wanted = set(concept_ids)
    return route.model_copy(
        update={
            "steps": [
                step.model_copy(update={"status": "claimed"})
                if step.concept_id in wanted and step.status == "ahead"
                else step
                for step in route.steps
            ]
        }
    )


def release_current(route: Route) -> Route:
    """Снятая с позиции «текущая» тема снова впереди (урок с чистого листа)."""
    return route.model_copy(
        update={
            "steps": [
                step.model_copy(update={"status": "ahead"}) if step.status == "current" else step
                for step in route.steps
            ]
        }
    )


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
    """Строит маршрут; узлы, закрытые раньше, сохраняют свой статус.

    Закрытая тема открывается снова, если повторение просрочено или после
    закрытия по ней набралось ``REOPEN_FAILURES`` провалов (§4.2 спеки
    модулей: путь «назад» через задания следующих модулей).
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    before = {step.concept_id: step for step in (previous.steps if previous else [])}

    # Заявленное в анкете: без снимка — из фактов, со снимком — из него. Узел,
    # переставший быть заявленным (стал текущим, проход не подтвердился),
    # обратно не возвращается.
    claimed_before = (
        {step.concept_id for step in previous.steps if step.status == "claimed"}
        if previous is not None
        else set(claimed_concepts(conn))
    )

    steps: list[RouteStep] = []
    for concept_id in _scope(graph, goal_concept_id):
        mode = planner.mode_for_node(conn, graph, concept_id, now=stamp, settings=s)
        old = before.get(concept_id)
        was_closed = old is not None and old.status == "closed"
        # Снимок до среза 25 времени закрытия не хранит: отсчёт — с этого пересчёта.
        closed_at = old.closed_at if was_closed and old.closed_at is not None else stamp
        # Закрытое остаётся закрытым, пока владение держится: просроченное
        # повторение (§6.4) или провалы после закрытия — например, по заданиям
        # следующих модулей, которые на тему опираются, — возвращают её.
        stays_closed = (
            was_closed
            and mode != "review"
            and _failures_since(conn, concept_id, closed_at) < REOPEN_FAILURES
        )
        if mode == "skip" or stays_closed:
            status = "closed"
        elif concept_id == current_node_id:
            status = "current"
        elif concept_id in claimed_before:
            status = "claimed"
        else:
            status = "ahead"
        # Открывшаяся снова тема хранит время прошлого закрытия — метку для
        # рабочего участка; никогда не закрытая — без метки.
        if status == "closed" or was_closed:
            mark = closed_at
        else:
            mark = old.closed_at if old is not None else None
        steps.append(
            RouteStep(concept_id=concept_id, mode=mode, status=status, closed_at=mark)
        )
    route = Route(
        goal_concept_id=goal_concept_id,
        steps=steps,
        completed_topics=_completed_topics(graph, previous),
    )
    previous_topic = previous.topic_id if previous is not None else None
    return route.model_copy(
        update={"topic_id": choose_topic(graph, route, previous_topic=previous_topic)}
    )


def diff_routes(
    previous: Route | None, current: Route, *, within: Collection[str] | None = None
) -> RouteChanges:
    """Сравнивает снимок с новым расчётом; ``within`` — только эти темы."""
    keep = None if within is None else set(within)

    def _steps(route: Route | None) -> dict[str, RouteStep]:
        return {
            step.concept_id: step
            for step in (route.steps if route is not None else [])
            if keep is None or step.concept_id in keep
        }

    def _current(steps: dict[str, RouteStep]) -> str | None:
        return next((node for node, step in steps.items() if step.status == "current"), None)

    old, new = _steps(previous), _steps(current)
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
        current_changed=_current(old) != _current(new),
    )


def is_significant(changes: RouteChanges, *, min_steps: int = 2) -> bool:
    """Стоит ли вообще говорить ученику о пересмотре (§6.4 — «малые различия»).

    Смена текущего узла сама по себе не считается: она всегда сопровождается
    закрытием узла, а сообщения о ней всё равно нечего составить.
    """
    return bool(changes.closed) or len(changes.added) + len(changes.removed) >= min_steps


def next_node_id(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    route: Route,
    *,
    now: float | None = None,
    settings: Settings | None = None,
    mastery_overrides: Mapping[str, beta.Mastery] | None = None,
) -> str | None:
    """Следующий узел маршрута: по приоритету среди готовых (§6.3).

    Приоритет считает планировщик — важность, пробел, готовность, срочность
    повторения, стоимость и штраф за провал. Маршрут задаёт путь, а не порядок
    прохода: пойти можно по любому узлу, у которого закрыты пререквизиты.
    Выбор — только внутри рабочего участка модуля работы (§4.1 спеки модулей):
    так модули идут по порядку.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    # Снимок до среза 25 модуля не знает — выбираем его по снимку.
    topic = route.topic_id if route.topic_id is not None else choose_topic(graph, route)
    if topic is None:
        return None
    section = set(working_section(graph, route, topic))
    closed = {step.concept_id for step in route.steps if step.status == "closed"}
    claimed = {step.concept_id for step in route.steps if step.status == "claimed"}
    # Заявленное — выполненный пререквизит: урок начинается с первого
    # неуверенного блока, а не с азов, которые ученик назвал знакомыми.
    ready = planner.ready_nodes(
        conn,
        graph,
        goal_concept_id=route.goal_concept_id,
        limit=max(len(section), 1),
        now=stamp,
        settings=s,
        mastery_overrides=mastery_overrides,
        completed_ids=frozenset(closed | claimed),
        scope=section,
    )
    for node in ready:
        # Готовый узел может быть уже закрытым — тогда он не «следующий».
        if node.concept_id not in closed | claimed:
            return node.concept_id
    # Незаявленное кончилось — пора подтвердить заявленное, по порядку маршрута.
    return next(
        (
            step.concept_id
            for step in route.steps
            if step.status == "claimed" and step.concept_id in section
        ),
        None,
    )


def upcoming(
    route: Route,
    first: str | None,
    *,
    limit: int,
    section: Collection[str] | None = None,
) -> list[RouteStep]:
    """Ближайшие шаги для списка: с какого начнём, потом незакрытые по порядку
    маршрута, в хвосте — заявленные (их проверим в конце)."""
    allowed = None if section is None else set(section)
    steps = [s for s in route.steps if allowed is None or s.concept_id in allowed]
    if first is None:
        return []
    head = [step for step in steps if step.concept_id == first]
    rest = [
        step
        for step in steps
        if step.status != "closed" and step.concept_id != first
    ]
    ahead = [step for step in rest if step.status != "claimed"]
    claimed = [step for step in rest if step.status == "claimed"]
    return (head + ahead + claimed)[:limit]


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

    # Ученику важен участок модуля: рост курса и чужие модули — не пересмотр
    # его плана. Темы модулей, которых в снимке не было вовсе, — тоже рост
    # курса, а не «добавилось» (аудит среза 25, H1).
    previous_ids = {step.concept_id for step in state.route.steps}
    known_topics = {
        graph.topic_of(node_id) for node_id in previous_ids if graph.has_node(node_id)
    }
    within = [
        node_id
        for node_id in working_section(graph, fresh, fresh.topic_id)
        if node_id in previous_ids or graph.topic_of(node_id) in known_topics
    ]
    changes = diff_routes(state.route, fresh, within=within)
    note = (
        format_route_change(changes)
        if is_significant(changes, min_steps=s.route_min_significant_changes)
        else None
    )
    return fresh, note
