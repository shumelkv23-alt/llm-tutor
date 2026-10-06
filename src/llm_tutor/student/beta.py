"""Beta-модель владения концептом (Срез 4.3).

Для каждого концепта хранятся псевдо-счётчики α (успехи) и β (неудачи) с
весами по типу свидетельства. Оценка владения = α/(α+β), а неопределённость
берётся из ширины апостериорного распределения — отдельного «мастерства»
LLM не выдаёт.

Забывание (decay) применяется **лениво при чтении**: значения стягиваются к
априору по времени с ``last_seen``, отдельный планировщик для этого не нужен.
"""

import math
import sqlite3
import time
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos

SECONDS_PER_DAY = 86_400.0

# Расписание повторения [эвристика]: чем увереннее владение, тем реже повторять.
# Интервал ~ mean/(1−mean) дней со страховкой снизу (знаменатель) и потолком.
REVIEW_BASE_DAYS = 1.0
REVIEW_MAX_DAYS = 30.0
_MIN_FAILURE_MASS = 0.05


def _next_review(mean: float, now: float) -> float:
    """Момент следующего повторения концепта (см. константы выше)."""
    ratio = mean / max(1.0 - mean, _MIN_FAILURE_MASS)
    days = min(REVIEW_MAX_DAYS, max(0.25 * REVIEW_BASE_DAYS, REVIEW_BASE_DAYS * ratio))
    return now + days * SECONDS_PER_DAY


@dataclass(frozen=True)
class Mastery:
    """Оценка владения концептом: счётчики, среднее и неопределённость."""

    concept_id: str
    alpha: float
    beta: float
    mean: float
    uncertainty: float
    last_seen: float | None
    next_review: float | None


@dataclass(frozen=True)
class MasteryUpdate:
    """Посчитанные счётчики концепта — ещё не записанные.

    Разделение «посчитать» и «записать» нужно, чтобы весь ход (реплики,
    события, модель ученика, состояние сессии) лёг одним коммитом.
    """

    concept_id: str
    alpha: float
    beta: float
    last_seen: float
    next_review: float | None


def decay(alpha: float, beta: float, dt_days: float, lam: float) -> tuple[float, float]:
    """Стягивает счётчики к априору Beta(1,1) за ``dt_days`` дней.

    ``x' = 1 + (x − 1)·exp(−λ·Δt)``; при ``dt_days <= 0`` значения не меняются.
    """
    factor = math.exp(-lam * max(dt_days, 0.0))
    return 1.0 + (alpha - 1.0) * factor, 1.0 + (beta - 1.0) * factor


def _to_mastery(
    concept_id: str,
    alpha: float,
    beta: float,
    last_seen: float | None,
    next_review: float | None,
) -> Mastery:
    """Собирает ``Mastery``: среднее и σ апостериорного Beta(α, β)."""
    total = alpha + beta
    mean = alpha / total
    variance = (alpha * beta) / (total * total * (total + 1.0))
    return Mastery(
        concept_id=concept_id,
        alpha=alpha,
        beta=beta,
        mean=mean,
        uncertainty=math.sqrt(variance),
        last_seen=last_seen,
        next_review=next_review,
    )


def _decayed_now(
    row: dict | None, settings: Settings, now: float
) -> tuple[float, float, float | None, float | None]:
    """Счётчики из строки БД с применённым ленивым decay к моменту ``now``."""
    if row is None:
        return settings.beta_prior_alpha, settings.beta_prior_beta, None, None

    alpha, beta = float(row["alpha"]), float(row["beta"])
    last_seen = row["last_seen"]
    if last_seen is not None:
        dt_days = (now - float(last_seen)) / SECONDS_PER_DAY
        alpha, beta = decay(alpha, beta, dt_days, settings.beta_decay_lambda)
    return alpha, beta, last_seen, row["next_review"]


def estimate(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> Mastery:
    """Текущая оценка владения (без записи в БД).

    Для незаведённого концепта возвращает априор Beta(1, 1) — «не знаем».
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    alpha, beta, last_seen, next_review = _decayed_now(
        repos.get_mastery(conn, concept_id), s, stamp
    )
    return _to_mastery(concept_id, alpha, beta, last_seen, next_review)


def plan_update(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    correct: float,
    weight: float = 1.0,
    now: float | None = None,
    settings: Settings | None = None,
) -> MasteryUpdate:
    """Считает новые счётчики: α += weight·correct, β += weight·(1−correct).

    Перед добавлением применяется decay от ``last_seen`` до ``now`` — иначе
    старые счётчики «капали» бы без затухания. ``correct`` — доля верного
    (обычно 0 или 1), ``weight`` — надёжность источника свидетельства.
    Заодно назначается ``next_review`` — момент следующего повторения.
    В БД ничего не пишет.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    alpha, beta, _, _ = _decayed_now(repos.get_mastery(conn, concept_id), s, stamp)
    alpha += weight * correct
    beta += weight * (1.0 - correct)

    return MasteryUpdate(
        concept_id=concept_id,
        alpha=alpha,
        beta=beta,
        last_seen=stamp,
        next_review=_next_review(alpha / (alpha + beta), stamp),
    )


def write_update(
    conn: sqlite3.Connection, change: MasteryUpdate, *, commit: bool = True
) -> None:
    """Записывает посчитанные счётчики концепта."""
    repos.upsert_mastery(
        conn,
        change.concept_id,
        alpha=change.alpha,
        beta=change.beta,
        last_seen=change.last_seen,
        next_review=change.next_review,
        commit=commit,
    )


def update(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    correct: float,
    weight: float = 1.0,
    now: float | None = None,
    settings: Settings | None = None,
) -> Mastery:
    """Добавляет свидетельство и сразу записывает его."""
    change = plan_update(
        conn, concept_id, correct=correct, weight=weight, now=now, settings=settings
    )
    write_update(conn, change)
    return _to_mastery(
        change.concept_id, change.alpha, change.beta, change.last_seen, change.next_review
    )


def plan_propagation(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    concept_id: str,
    *,
    weight: float = 1.0,
    now: float | None = None,
    settings: Settings | None = None,
) -> list[MasteryUpdate]:
    """Считает слабый подъём прямых пререквизитов успешного узла (без записи).

    Идея теории пространств знаний: решил продвинутое — вероятно, знаешь
    базовое. Вес малый (``beta_propagate_weight``), чтобы одно случайное
    решение не «обрушило» оценки по графу. ``next_review`` не трогаем: ученик
    пререквизит не изучал.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    amount = s.beta_propagate_weight * weight
    if amount <= 0.0:
        return []

    changes: list[MasteryUpdate] = []
    for prereq_id in graph.prerequisites(concept_id):
        alpha, beta, _, _ = _decayed_now(repos.get_mastery(conn, prereq_id), s, stamp)
        changes.append(
            MasteryUpdate(
                concept_id=prereq_id,
                alpha=alpha + amount,
                beta=beta,
                last_seen=stamp,
                next_review=None,
            )
        )
    return changes


def propagate_success(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    concept_id: str,
    *,
    weight: float = 1.0,
    now: float | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """Поднимает прямые пререквизиты успешного узла и сразу записывает."""
    changes = plan_propagation(
        conn, graph, concept_id, weight=weight, now=now, settings=settings
    )
    for change in changes:
        write_update(conn, change)
    return [change.concept_id for change in changes]
