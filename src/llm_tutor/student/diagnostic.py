"""Выдача заданий по узлу и запись свидетельств (4.8).

Для узла берётся самое информативное задание банка (сложность ≈ текущая
оценка владения, ≈50% ожидаемого успеха), ответ обновляет Beta, а успех
слабо поднимает прямые пререквизиты. Зовёт ядро: заход урока и
проверочный проход (``core.turn``).

Адаптивная диагностика бота (``/diagnostic``) удалена вместе с ботом (Срез 49).
"""

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Event, EventSource, Item
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


def _available_items(
    conn: sqlite3.Connection, *, include_rubric: bool, include_runnable: bool = False
) -> list[Item]:
    """Активные задания банка, пригодные для выдачи.

    ``include_rubric`` добавляет открытые и код-задания с рубрикой — их
    проверяет грейдер (заход урока их не берёт, см. ``verification_item``).
    ``include_runnable`` добавляет код с тестами (его проверяют тесты в
    браузере).
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
        elif include_runnable and item.runnable:
            # Код с тестами проверяет код (тесты в браузере), а не модель: ему
            # место и в уроке. Ответ текстом проверит рубрика задания.
            items.append(item)
        elif item.answer_type in RUBRIC_CHECKABLE and item.rubric_id in gradable_rubrics:
            # Задание без активных критериев выдать нельзя: проверить нечем.
            items.append(item)
    return items


def _best_item(items: Iterable[Item], concept_id: str, target_difficulty: float) -> Item | None:
    """Самое информативное задание по узлу: сложность ближе всего к цели."""
    candidates = [item for item in items if concept_id in item.concept_weights]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (abs(item.difficulty - target_difficulty), item.id))


def verification_item(
    conn: sqlite3.Connection,
    node_id: str,
    *,
    used_item_ids: frozenset[int] = frozenset(),
    include_rubric: bool = True,
    now: float | None = None,
    settings: Settings | None = None,
) -> DiagnosticQuestion | None:
    """Задание захода по узлу: объяснение приоритетнее автопроверки.

    Паузы перед повтором нет: ученик явно просит проверить, а банк по
    большинству узлов содержит пару заданий — с паузой проверять было бы
    нечем. От накрутки защищает ``used_item_ids``: внутри одного захода
    задание не выдаётся дважды.

    ``include_rubric=False`` — заход ведомого урока: рубричные задания
    остаются проверочному проходу, там ученик доказывает знание словами
    (§5.3), а урок не должен зависеть от вердикта грейдера.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    items = [
        item
        for item in _available_items(conn, include_rubric=include_rubric, include_runnable=True)
        if item.id not in used_item_ids and node_id in item.concept_weights
    ]
    if not items:
        return None

    target = beta.estimate(conn, node_id, now=stamp, settings=s).mean
    # Объяснение первым: оно несёт больше сигнала, чем выбор варианта.
    rubric_items = [item for item in items if item.answer_type in RUBRIC_CHECKABLE]
    candidate = _best_item(rubric_items or items, node_id, target_difficulty=target)
    if candidate is None:
        return None
    return DiagnosticQuestion(item=candidate, concept_id=node_id)


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
    # По концепту могло накопиться два обновления (прямое и распространение):
    # пишутся абсолютные счётчики, поэтому без слияния прямое потерялось бы.
    return events, beta.merge_updates(conn, mastery, now=stamp, settings=s)
