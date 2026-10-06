"""Тесты Beta-модели владения (Срез 4.3)."""

import math

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge
from llm_tutor.student import beta

DAY = beta.SECONDS_PER_DAY
# σ апостериорного Beta(1, 1) — стартовая неопределённость «не знаем».
PRIOR_UNCERTAINTY = math.sqrt(1.0 / 12.0)


def _concept(conn, concept_id: str) -> None:
    """Заводит концепт — нужен для FK из mastery."""
    repos.upsert_concept(conn, Concept(id=concept_id, name=concept_id))


@pytest.fixture
def graph() -> CourseGraph:
    """a → b (жёстко), c → b (мягко), b → d (жёстко)."""
    return CourseGraph(
        [Concept(id=name, name=name) for name in ("a", "b", "c", "d")],
        [
            Edge(from_id="a", to_id="b", hard=True),
            Edge(from_id="c", to_id="b", hard=False),
            Edge(from_id="b", to_id="d", hard=True),
        ],
    )


# --- decay (чистая функция) ---


def test_decay_without_elapsed_time_is_identity() -> None:
    assert beta.decay(4.0, 3.0, 0.0, 0.05) == (4.0, 3.0)
    assert beta.decay(4.0, 3.0, -5.0, 0.05) == (4.0, 3.0)


def test_decay_pulls_counters_toward_prior() -> None:
    alpha, b = beta.decay(5.0, 2.0, 30.0, 0.05)

    assert alpha == pytest.approx(1.0 + 4.0 * math.exp(-1.5))
    assert b == pytest.approx(1.0 + 1.0 * math.exp(-1.5))
    assert 1.0 < alpha < 5.0 and 1.0 < b < 2.0


def test_decay_never_drops_below_prior() -> None:
    alpha, b = beta.decay(3.0, 1.0, 10_000.0, 0.05)

    assert alpha == pytest.approx(1.0)
    assert b == pytest.approx(1.0)


# --- estimate ---


def test_unknown_concept_returns_prior(conn, settings) -> None:
    mastery = beta.estimate(conn, "ghost", now=0.0, settings=settings)

    assert (mastery.alpha, mastery.beta) == (1.0, 1.0)
    assert mastery.mean == pytest.approx(0.5)
    assert mastery.uncertainty == pytest.approx(PRIOR_UNCERTAINTY)
    assert mastery.last_seen is None


def test_estimate_decays_stored_counters_lazily(conn, settings) -> None:
    _concept(conn, "a")
    repos.upsert_mastery(conn, "a", alpha=6.0, beta=2.0, last_seen=0.0)

    mastery = beta.estimate(conn, "a", now=30 * DAY, settings=settings)

    assert mastery.alpha == pytest.approx(1.0 + 5.0 * math.exp(-1.5))
    # Сырые счётчики в БД не перезаписаны — decay ленивый.
    assert repos.get_mastery(conn, "a")["alpha"] == 6.0


# --- update ---


def test_update_counts_success_and_failure(conn, settings) -> None:
    _concept(conn, "a")

    success = beta.update(conn, "a", correct=1.0, weight=1.0, now=0.0, settings=settings)
    assert (success.alpha, success.beta) == (2.0, 1.0)
    assert success.mean == pytest.approx(2.0 / 3.0)

    failure = beta.update(conn, "a", correct=0.0, weight=1.0, now=0.0, settings=settings)
    assert (failure.alpha, failure.beta) == (2.0, 2.0)
    assert failure.mean == pytest.approx(0.5)


def test_update_applies_decay_before_adding(conn, settings) -> None:
    _concept(conn, "a")
    beta.update(conn, "a", correct=1.0, weight=1.0, now=0.0, settings=settings)

    # Через 30 дней α стянулся к априору, затем добавился новый успех.
    updated = beta.update(conn, "a", correct=1.0, weight=1.0, now=30 * DAY, settings=settings)

    assert updated.alpha == pytest.approx(1.0 + 1.0 * math.exp(-1.5) + 1.0)
    assert updated.last_seen == 30 * DAY


def test_update_schedules_next_review(conn, settings) -> None:
    """update назначает повторение — иначе режим «повторение» в проде мёртв."""
    _concept(conn, "a")

    result = beta.update(conn, "a", correct=1.0, now=0.0, settings=settings)

    assert result.next_review is not None
    assert repos.get_mastery(conn, "a")["next_review"] == result.next_review


def test_next_review_grows_with_mastery(conn, settings) -> None:
    """Чем увереннее владение, тем дальше следующий повтор."""
    _concept(conn, "a")
    weak = beta.update(conn, "a", correct=0.0, now=0.0, settings=settings)

    strong = weak
    for _ in range(6):
        strong = beta.update(conn, "a", correct=1.0, now=0.0, settings=settings)

    assert strong.next_review > weak.next_review


def test_estimate_preserves_stored_next_review(conn, settings) -> None:
    """Ленивое чтение не теряет назначенное повторение."""
    _concept(conn, "a")
    repos.upsert_mastery(conn, "a", alpha=5.0, beta=1.0, last_seen=0.0, next_review=999.0)

    assert beta.estimate(conn, "a", now=0.0, settings=settings).next_review == 999.0


def test_more_evidence_lowers_uncertainty(conn, settings) -> None:
    _concept(conn, "a")
    before = beta.estimate(conn, "a", now=0.0, settings=settings)

    for _ in range(3):
        after = beta.update(conn, "a", correct=1.0, now=0.0, settings=settings)

    assert after.uncertainty < before.uncertainty


# --- propagate ---


def test_propagate_updates_only_direct_prerequisites(conn, settings, graph) -> None:
    for name in ("a", "b", "c", "d"):
        _concept(conn, name)

    updated = beta.propagate_success(conn, graph, "b", now=0.0, settings=settings)

    assert updated == ["a", "c"]  # пререквизиты b, но не зависимый d
    assert repos.get_mastery(conn, "a")["alpha"] == pytest.approx(
        1.0 + settings.beta_propagate_weight
    )
    assert repos.get_mastery(conn, "d") is None


def test_propagate_amount_scales_with_weight(conn, settings, graph) -> None:
    for name in ("a", "b", "c"):
        _concept(conn, name)

    beta.propagate_success(conn, graph, "b", weight=2.0, now=0.0, settings=settings)

    assert repos.get_mastery(conn, "a")["alpha"] == pytest.approx(
        1.0 + 2.0 * settings.beta_propagate_weight
    )


def test_propagate_with_zero_amount_is_noop(conn, settings, graph) -> None:
    _concept(conn, "a")
    zero_weight = settings.model_copy(update={"beta_propagate_weight": 0.0})

    assert beta.propagate_success(conn, graph, "b", now=0.0, settings=zero_weight) == []
    assert repos.get_mastery(conn, "a") is None
