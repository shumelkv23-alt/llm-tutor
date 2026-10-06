"""Планировщик маршрута: готовые узлы, приоритет и режимы прохода (Срез 4.4).

Узел «готов» (в границах готовности), когда все его жёсткие пререквизиты
освоены; мягкие пререквизиты не блокируют, а лишь роняют приоритет.
Приоритет — линейная комбинация нормализованных слагаемых (см. архитектуру,
§6.3); режим прохода выбирается по порогам владения с приоритетом более
специфичных случаев (повторение / буксовка).
"""

import sqlite3
import time

from llm_tutor.config import Settings, get_settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import NodeMode, PlannedNode
from llm_tutor.student import beta

# Окно «недавнего провала» (дни) и число провалов, дающее полный штраф.
FAILURE_WINDOW_DAYS = 3.0
FAILURE_PENALTY_CAP = 2.0
# Апостериорная σ ниже порога = владение подтверждено, можно пропускать.
CONFIDENT_UNCERTAINTY = 0.10
# Просрочка повторения на этот срок даёт максимальную срочность.
REVIEW_PERIOD_SECONDS = 7 * 86_400.0
# Столько недавних провалов подряд трактуем как «буксовку» (wheel spinning).
STUCK_FAILURES = 3


def _recent_failures(
    conn: sqlite3.Connection, concept_id: str, now: float, window_days: float
) -> int:
    """Число недавних неудачных событий по концепту (в окне ``window_days``)."""
    threshold = now - window_days * beta.SECONDS_PER_DAY
    return sum(
        1
        for event in repos.get_events(conn, concept_id)
        if event.ts is not None and event.ts >= threshold and event.result < 0.5
    )


def _goal_relevance(node_id: str, goal_concept_id: str | None) -> float:
    """1.0, если узел нужен для цели (вне цели этот компонент выключен)."""
    return 1.0 if goal_concept_id is not None else 0.0


def _readiness(graph: CourseGraph, node_id: str, means: dict[str, beta.Mastery], threshold: float) -> float:
    """Взвешенная доля пререквизитов, доведённых до порога освоения (0..1)."""
    prereqs = graph.prerequisites(node_id)
    total_weight = sum(graph.edge_weight(p, node_id) for p in prereqs)
    if total_weight <= 0.0:
        return 1.0
    covered = sum(
        graph.edge_weight(p, node_id) * min(1.0, means[p].mean / threshold) for p in prereqs
    )
    return covered / total_weight


def _urgency(mastery: beta.Mastery, now: float) -> float:
    """Срочность повторения: 0 — не назначено, →1 — сильно просрочено."""
    if mastery.next_review is None:
        return 0.0
    return min(1.0, max(0.0, (now - mastery.next_review) / REVIEW_PERIOD_SECONDS))


def _choose_mode(
    mastery: beta.Mastery,
    *,
    weak_soft_prereq: bool,
    overdue: bool,
    stuck: bool,
    settings: Settings,
) -> NodeMode:
    """Режим прохода узла по владению с приоритетом специфичных случаев."""
    if overdue and mastery.mean >= settings.mastery_compressed_threshold:
        return "review"
    if stuck:
        return "reinforce"
    if (
        mastery.mean >= settings.mastery_skip_threshold
        and mastery.uncertainty <= CONFIDENT_UNCERTAINTY
    ):
        return "skip"
    if mastery.mean >= settings.mastery_verify_threshold:
        return "verify"
    if mastery.mean < settings.mastery_compressed_threshold and weak_soft_prereq:
        return "revisit"
    if mastery.mean >= settings.mastery_compressed_threshold:
        return "compressed"
    return "full"


def ready_nodes(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    *,
    goal_concept_id: str | None = None,
    limit: int = 3,
    now: float | None = None,
    settings: Settings | None = None,
) -> list[PlannedNode]:
    """Узлы границы готовности, отсортированные по приоритету (топ-``limit``).

    Жёсткие пререквизиты проверяются по порогу ``mastery_verify_threshold``.
    Узлы с режимом ``skip`` (уверенно освоены) в маршрут не попадают.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    if limit <= 0:
        return []

    means = {node_id: beta.estimate(conn, node_id, now=stamp, settings=s) for node_id in graph.node_ids}

    scope = set(graph.node_ids)
    if goal_concept_id is not None:
        graph.concept(goal_concept_id)  # падает, если цели нет в графе
        scope = graph.ancestors(goal_concept_id) | {goal_concept_id}

    candidates = [
        node_id
        for node_id in scope
        if all(
            means[p].mean >= s.mastery_verify_threshold
            for p in graph.hard_prerequisites(node_id)
        )
    ]
    if not candidates:
        return []

    max_descendants = max(len(graph.descendants(n)) for n in candidates) or 1

    scored: list[tuple[str, float, NodeMode]] = []
    for node_id in candidates:
        mastery = means[node_id]
        failures = _recent_failures(conn, node_id, stamp, FAILURE_WINDOW_DAYS)
        overdue = mastery.next_review is not None and stamp >= mastery.next_review
        weak_soft = any(
            means[p].mean < s.mastery_verify_threshold
            for p in graph.soft_prerequisites(node_id)
        )
        mode = _choose_mode(
            mastery,
            weak_soft_prereq=weak_soft,
            overdue=overdue,
            stuck=failures >= STUCK_FAILURES,
            settings=s,
        )
        if mode == "skip":
            continue

        importance = 0.5 * _goal_relevance(node_id, goal_concept_id) + 0.5 * (
            len(graph.descendants(node_id)) / max_descendants
        )
        priority = (
            s.priority_w1 * importance
            + s.priority_w2 * (1.0 - mastery.mean)
            + s.priority_w3 * _readiness(graph, node_id, means, s.mastery_verify_threshold)
            + s.priority_w4 * _urgency(mastery, stamp)
            - s.priority_w5 * graph.difficulty(node_id)
            - s.priority_w6 * min(failures / FAILURE_PENALTY_CAP, 1.0)
        )
        scored.append((node_id, priority, mode))

    if not scored:
        return []

    raw = [priority for _, priority, _ in scored]
    low, high = min(raw), max(raw)
    span = high - low
    nodes = [
        PlannedNode(
            concept_id=node_id,
            mode=mode,
            priority=1.0 if span == 0.0 else (priority - low) / span,
        )
        for node_id, priority, mode in scored
    ]
    nodes.sort(key=lambda node: (-node.priority, node.concept_id))
    return nodes[:limit]
