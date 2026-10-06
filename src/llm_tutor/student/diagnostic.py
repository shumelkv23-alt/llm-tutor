"""Адаптивная диагностика: выбор узла и задания, запись свидетельств (4.8).

Выбираем узел с наибольшей неопределённостью с поправкой на важность (сколько
зависимых узлов он открывает), для него — самое информативное задание банка
(сложность ≈ текущая оценка владения). Ответ проверяет код (autocheck), затем
Beta обновляется, а успех слабо поднимает прямые пререквизиты.

Диагностика целится в максимум информации, а не в пользу для обучения: отсюда
целевая сложность ≈ текущему владению (≈50% ожидаемого успеха) — это не
противоречит обучению в зоне ближайшего развития (там цель 60–80%).
"""

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.grader import autocheck
from llm_tutor.schemas import Event, EventSource, GradeResult, Item
from llm_tutor.student import beta

# Типы заданий, которые диагностика умеет проверять сама (без LLM).
AUTO_CHECKABLE = ("choice", "short")
# Типы, которые проверяет рубричный грейдер (Срез 6).
RUBRIC_CHECKABLE = ("open", "code")
# Ответ с таким вердиктом считается успехом (для распространения по графу).
SUCCESS_SCORE = 0.5


@dataclass(frozen=True)
class DiagnosticQuestion:
    """Задание вместе с узлом, который им измеряем."""

    item: Item
    concept_id: str


def uncertainty_priority(
    graph: CourseGraph, concept_id: str, mastery: beta.Mastery
) -> float:
    """Приоритет диагностики: неопределённость × важность (число зависимых)."""
    return mastery.uncertainty * (1 + len(graph.descendants(concept_id)))


def _available_items(
    conn: sqlite3.Connection, *, include_rubric: bool
) -> list[Item]:
    """Активные задания банка, пригодные для выдачи.

    ``include_rubric`` добавляет открытые и код-задания с рубрикой — их
    проверяет грейдер, поэтому диагностика (которая считает сама) их не берёт.
    """
    gradable_rubrics = (
        {
            rubric.id
            for rubric in repos.get_rubrics(conn)
            if repos.get_criteria(conn, rubric.id)
        }
        if include_rubric
        else set()
    )

    items = []
    for item in repos.get_items(conn):
        if item.answer_type in AUTO_CHECKABLE and item.answer is not None:
            items.append(item)
        elif item.answer_type in RUBRIC_CHECKABLE and item.rubric_id in gradable_rubrics:
            # Задание без активных критериев выдать нельзя: проверить нечем.
            items.append(item)
    return items


def _freshly_answered_items(
    conn: sqlite3.Connection, now: float, cooldown_days: float
) -> set[int]:
    """Задания, отвеченные недавно: повтор сейчас накрутил бы счётчики."""
    if cooldown_days <= 0:
        return set()
    threshold = now - cooldown_days * beta.SECONDS_PER_DAY
    return {
        event.item_id
        for event in repos.get_events(conn)
        if event.item_id is not None
        and event.ts is not None
        and event.ts >= threshold
    }


def _best_item(items: Iterable[Item], concept_id: str, target_difficulty: float) -> Item | None:
    """Самое информативное задание по узлу: сложность ближе всего к цели."""
    candidates = [item for item in items if concept_id in item.concept_weights]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (abs(item.difficulty - target_difficulty), item.id))


def next_question(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    *,
    asked_item_ids: frozenset[int] = frozenset(),
    include_rubric: bool = False,
    now: float | None = None,
    settings: Settings | None = None,
) -> DiagnosticQuestion | None:
    """Следующее задание диагностики или ``None``, если спрашивать нечего.

    Останавливаемся, когда у всех узлов неопределённость ниже порога либо
    для них не осталось незаданных заданий в банке.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    unavailable = set(asked_item_ids) | _freshly_answered_items(
        conn, stamp, s.item_repeat_cooldown_days
    )
    items = [
        item
        for item in _available_items(conn, include_rubric=include_rubric)
        if item.id not in unavailable
    ]
    if not items:
        return None

    scored: list[tuple[float, str, float]] = []
    for concept_id in graph.node_ids:
        mastery = beta.estimate(conn, concept_id, now=stamp, settings=s)
        if mastery.uncertainty <= s.diagnostic_uncertainty_threshold:
            continue
        scored.append((uncertainty_priority(graph, concept_id, mastery), concept_id, mastery.mean))
    scored.sort(key=lambda triple: (-triple[0], triple[1]))

    for _, concept_id, mean in scored:
        item = _best_item(items, concept_id, target_difficulty=mean)
        if item is not None:
            return DiagnosticQuestion(item=item, concept_id=concept_id)
    return None


def question_for_node(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    asked_item_ids: frozenset[int] = frozenset(),
    now: float | None = None,
    settings: Settings | None = None,
) -> DiagnosticQuestion | None:
    """Задание именно по этому узлу (ведение занятия, а не диагностика).

    Отличие от ``next_question``: узел задан маршрутом, а не выбран по
    неопределённости. Целевая сложность — текущее владение узлом.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    unavailable = set(asked_item_ids) | _freshly_answered_items(
        conn, stamp, s.item_repeat_cooldown_days
    )
    items = [
        item
        for item in _available_items(conn, include_rubric=True)
        if item.id not in unavailable
    ]
    target = beta.estimate(conn, node_id, now=stamp, settings=s).mean
    item = _best_item(items, node_id, target_difficulty=target)
    return DiagnosticQuestion(item=item, concept_id=node_id) if item is not None else None


def plan_evidence(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    item: Item,
    concept_id: str,
    score: float,
    *,
    source: EventSource = "checked",
    weight_scale: float = 1.0,
    now: float | None = None,
    settings: Settings | None = None,
) -> tuple[list[Event], list[beta.MasteryUpdate]]:
    """Считает свидетельства по вердикту (события + обновления модели).

    ``weight_scale`` ограничивает вес свидетельства: вердикт модели по рубрике
    слабее проверки кодом и не должен в одиночку «закрывать» тему.

    Ничего не пишет: вызывающий сам решает, коммитить сразу или вместе с
    остальными записями хода (``core.turn.post_turn``).
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    events: list[Event] = []
    mastery: list[beta.MasteryUpdate] = []
    for weight_concept, weight in item.concept_weights.items():
        scaled = weight * weight_scale
        events.append(
            Event(
                source=source,
                result=score,
                concept_id=weight_concept,
                item_id=item.id,
                weight=scaled,
                ts=stamp,
            )
        )
        mastery.append(
            beta.plan_update(
                conn, weight_concept, correct=score, weight=scaled, now=stamp, settings=s
            )
        )

    # Успех поднимает пререквизиты измеренного узла. Если задание вдруг не
    # описывает этот узел (seed поменялся между показом и ответом) — не трогаем
    # чужие пререквизиты.
    if (
        score >= SUCCESS_SCORE
        and concept_id in item.concept_weights
        and graph.has_node(concept_id)
    ):
        mastery.extend(
            beta.plan_propagation(
                conn,
                graph,
                concept_id,
                weight=item.concept_weights[concept_id] * weight_scale,
                now=stamp,
                settings=s,
            )
        )
    return events, mastery


def record_answer(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    question: DiagnosticQuestion,
    answer: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> GradeResult:
    """Проверяет ответ кодом, пишет события и обновляет модель ученика."""
    stamp = time.time() if now is None else now
    result = autocheck.check(question.item, answer)
    events, mastery = plan_evidence(
        conn,
        graph,
        question.item,
        question.concept_id,
        result.score,
        now=stamp,
        settings=settings,
    )
    for event in events:
        repos.add_event(conn, event, stamp)
    for change in mastery:
        beta.write_update(conn, change)
    return result


def questions_for_pass(
    conn: sqlite3.Connection, *, settings: Settings | None = None
) -> int:
    """Длина захода: первый (пока нет проверенных ответов) шире последующих."""
    s = settings or get_settings()
    has_checked = any(event.source == "checked" for event in repos.get_events(conn))
    return s.diagnostic_followup if has_checked else s.diagnostic_first_pass
