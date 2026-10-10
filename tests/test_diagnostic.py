"""Выдача заданий по узлу и запись свидетельств (Срез 4.8).

Адаптивная диагностика бота удалена в срезе 49; запись ответа здесь —
та же последовательность, что в ядре: автопроверка, plan_evidence, запись.
"""

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.grader import autocheck
from llm_tutor.schemas import Concept, Edge, Event, Item, SessionState
from llm_tutor.student import beta, diagnostic


def _graph(concepts: list[str], edges: list[tuple[str, str]] = ()) -> CourseGraph:
    return CourseGraph(
        [Concept(id=cid, name=cid) for cid in concepts],
        [Edge(from_id=src, to_id=dst, hard=True) for src, dst in edges],
    )


def _concept(conn, concept_id: str) -> None:
    repos.upsert_concept(conn, Concept(id=concept_id, name=concept_id))


def _item(
    conn,
    *,
    item_id: int,
    concept_id: str,
    difficulty: float = 0.5,
    answer_type: str = "choice",
    answer: str = "0",
) -> Item:
    item = Item(
        id=item_id,
        prompt=f"вопрос {item_id}",
        answer_type=answer_type,
        concept_weights={concept_id: 1.0},
        difficulty=difficulty,
        options=["верно", "неверно"] if answer_type == "choice" else [],
        answer=answer,
    )
    repos.upsert_item(conn, item)
    return item


def _record(conn, graph, item: Item, concept_id: str, answer: str, *, settings):
    """Ответ на задание с автопроверкой — как его записывает ядро."""
    result = autocheck.check(item, answer)
    events, mastery = diagnostic.plan_evidence(
        conn, graph, item, concept_id, result.score, now=0.0, settings=settings
    )
    for event in events:
        repos.add_event(conn, event, 0.0)
    for change in mastery:
        beta.write_update(conn, change)
    return result


# --- банк заданий ---


def test_rubric_item_without_criteria_is_not_offered(conn, settings) -> None:
    """Задание, которое нечем проверить, выдавать нельзя."""
    load_seed(conn)
    conn.execute("UPDATE criteria SET active = 0 WHERE rubric_id = 1")
    conn.commit()

    ids = {item.id for item in diagnostic._available_items(conn, include_rubric=True)}

    assert 9 not in ids  # рубрика 1 осталась без критериев
    assert 10 in ids


def test_open_item_needs_rubric_mode(conn, settings) -> None:
    """Открытое задание проверяет грейдер — без include_rubric его не выдают."""
    _concept(conn, "a")
    _item(conn, item_id=1, concept_id="a", answer_type="open", answer="эталон")

    assert diagnostic._available_items(conn, include_rubric=False) == []


def test_prefers_item_closest_to_current_mastery(conn, settings) -> None:
    """Целевая сложность ≈ текущему владению — задание информативнее."""
    _concept(conn, "a")
    _item(conn, item_id=1, concept_id="a", difficulty=0.9)
    _item(conn, item_id=2, concept_id="a", difficulty=0.5)

    question = diagnostic.verification_item(
        conn, "a", include_rubric=False, now=0.0, settings=settings
    )

    assert question is not None
    assert question.item.id == 2  # априор 0.5 ближе к 0.5, чем 0.9


# --- запись ответа ---


def test_record_correct_answer_updates_mastery_and_event(conn, settings) -> None:
    _concept(conn, "a")
    graph = _graph(["a"])
    item = _item(conn, item_id=1, concept_id="a", answer_type="short", answer="read_csv")

    result = _record(conn, graph, item, "a", "  READ_CSV ", settings=settings)

    assert result.score == 1.0
    assert beta.estimate(conn, "a", now=0.0, settings=settings).mean > 0.5
    (event,) = repos.get_events(conn, "a")
    assert (event.source, event.item_id, event.result) == ("checked", 1, 1.0)


def test_record_wrong_answer_lowers_mastery(conn, settings) -> None:
    _concept(conn, "a")
    graph = _graph(["a"])
    item = _item(conn, item_id=1, concept_id="a", answer_type="short", answer="read_csv")

    result = _record(conn, graph, item, "a", "read_excel", settings=settings)

    assert result.score == 0.0
    assert beta.estimate(conn, "a", now=0.0, settings=settings).mean < 0.5


def test_success_propagates_to_prerequisites(conn, settings) -> None:
    for cid in ("a", "b"):
        _concept(conn, cid)
    graph = _graph(["a", "b"], [("a", "b")])
    item = _item(conn, item_id=1, concept_id="b")

    _record(conn, graph, item, "b", "0", settings=settings)

    prereq = repos.get_mastery(conn, "a")
    assert prereq["alpha"] == pytest.approx(1.0 + settings.beta_propagate_weight)


def test_failure_does_not_propagate(conn, settings) -> None:
    for cid in ("a", "b"):
        _concept(conn, cid)
    graph = _graph(["a", "b"], [("a", "b")])
    item = _item(conn, item_id=1, concept_id="b")

    _record(conn, graph, item, "b", "1", settings=settings)

    assert repos.get_mastery(conn, "a") is None


# --- задание проверочного прохода (Срез 16) ---


def test_verification_item_prefers_rubric_item(conn, settings) -> None:
    """Объяснение идёт первым: рубричное задание приоритетнее автопроверки."""
    load_seed(conn)

    question = diagnostic.verification_item(
        conn, "groupby", used_item_ids=frozenset(), now=1.0, settings=settings
    )

    assert question is not None
    assert question.item.answer_type in diagnostic.RUBRIC_CHECKABLE


def test_verification_item_uses_repeat_if_nothing_fresh(conn, settings) -> None:
    """Пауза повтора в проходе не применяется: проверять иначе нечем."""
    load_seed(conn)
    item = repos.get_item(conn, 1)
    repos.add_event(
        conn,
        Event(source="checked", result=1.0, item_id=item.id, concept_id="pandas_intro", ts=1.0),
    )

    question = diagnostic.verification_item(
        conn, "pandas_intro", used_item_ids=frozenset(), now=1.0, settings=settings
    )

    assert question is not None
    assert question.item.id == item.id


def test_verification_item_skips_already_used(conn, settings) -> None:
    """В одном проходе одно задание не выдаётся дважды."""
    load_seed(conn)
    used = frozenset(
        item.id
        for item in repos.get_items(conn)
        if "pandas_intro" in item.concept_weights
    )

    question = diagnostic.verification_item(
        conn, "pandas_intro", used_item_ids=used, now=1.0, settings=settings
    )

    assert question is None


def test_verification_item_without_items_for_node(conn, settings) -> None:
    """У узла без заданий подбирать нечего."""
    load_seed(conn)
    # Банк среза 20 покрывает все узлы: пустой узел делаем руками — случай
    # остаётся страховкой на случай правки seed.
    conn.execute(
        # GLOB, а не LIKE: в LIKE «_» — подстановочный знак, и шаблон погасил бы
        # ещё и чужие задания с похожим id узла.
        "UPDATE items SET active = 0 WHERE concept_weights GLOB '*visualization_basics*'"
    )
    conn.commit()

    assert (
        diagnostic.verification_item(
            conn, "visualization_basics", used_item_ids=frozenset(), now=1.0, settings=settings
        )
        is None
    )


def test_verify_item_ids_defaults_to_empty() -> None:
    """Старая сессия без поля читается: дефолт пустой."""
    assert SessionState().verify_item_ids == []


def test_plan_evidence_sums_direct_and_propagated_evidence(conn, settings) -> None:
    """Прямое свидетельство не теряется под распространением по графу.

    Задание 9 меряет и `groupby` (прямо), и через `agg_functions` (по графу):
    без слияния абсолютных счётчиков прямое свидетельство затиралось бы.
    """
    load_seed(conn)
    graph = CourseGraph.load(conn)
    item = repos.get_item(conn, 9)
    weight_scale = settings.rubric_evidence_weight

    _, mastery = diagnostic.plan_evidence(
        conn, graph, item, "agg_functions", 1.0, weight_scale=weight_scale, now=1.0,
        settings=settings,
    )

    concepts = [change.concept_id for change in mastery]
    assert len(concepts) == len(set(concepts))  # по концепту ровно одно обновление
    direct = item.concept_weights["groupby"] * weight_scale
    propagated = (
        settings.beta_propagate_weight
        * item.concept_weights["agg_functions"]
        * weight_scale
    )
    groupby = next(change for change in mastery if change.concept_id == "groupby")
    assert groupby.alpha == pytest.approx(1.0 + direct + propagated)
