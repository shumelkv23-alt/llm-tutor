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
from llm_tutor.schemas import Event, GradeResult, Item
from llm_tutor.student import beta

# Типы заданий, которые диагностика умеет проверять сама (без LLM).
AUTO_CHECKABLE = ("choice", "short")
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


def _checkable_items(conn: sqlite3.Connection) -> list[Item]:
    """Задания банка, пригодные для автопроверки."""
    return [
        item
        for item in repos.get_items(conn)
        if item.answer_type in AUTO_CHECKABLE and item.answer is not None
    ]


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
    now: float | None = None,
    settings: Settings | None = None,
) -> DiagnosticQuestion | None:
    """Следующее задание диагностики или ``None``, если спрашивать нечего.

    Останавливаемся, когда у всех узлов неопределённость ниже порога либо
    для них не осталось незаданных заданий в банке.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    items = [item for item in _checkable_items(conn) if item.id not in asked_item_ids]
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
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    result = autocheck.check(question.item, answer)

    for concept_id, weight in question.item.concept_weights.items():
        repos.add_event(
            conn,
            Event(
                source="checked",
                result=result.score,
                concept_id=concept_id,
                item_id=question.item.id,
                weight=weight,
                ts=stamp,
            ),
        )
        beta.update(
            conn, concept_id, correct=result.score, weight=weight, now=stamp, settings=s
        )

    if result.score >= SUCCESS_SCORE:
        beta.propagate_success(
            conn,
            graph,
            question.concept_id,
            weight=question.item.concept_weights.get(question.concept_id, 1.0),
            now=stamp,
            settings=s,
        )
    return result


def questions_for_pass(
    conn: sqlite3.Connection, *, settings: Settings | None = None
) -> int:
    """Длина захода: первый (пока нет проверенных ответов) шире последующих."""
    s = settings or get_settings()
    has_checked = any(event.source == "checked" for event in repos.get_events(conn))
    return s.diagnostic_followup if has_checked else s.diagnostic_first_pass
