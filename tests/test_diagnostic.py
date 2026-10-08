"""Тесты адаптивной диагностики (Срез 4.8)."""

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Event, Item, SessionState
from llm_tutor.course.seed import load_seed
from llm_tutor.student import beta, diagnostic
from llm_tutor.student.diagnostic import (
    DiagnosticQuestion,
    next_question,
    questions_for_pass,
    record_answer,
    uncertainty_priority,
)


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


# --- выбор узла ---


def test_uncertainty_priority_grows_with_dependents() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("b", "c")])
    mastery = beta.Mastery("a", 1.0, 1.0, 0.5, 0.28, None, None)

    assert uncertainty_priority(graph, "a", mastery) > uncertainty_priority(graph, "c", mastery)


def test_picks_node_with_highest_uncertainty(conn, settings) -> None:
    for cid in ("a", "c"):
        _concept(conn, cid)
    graph = _graph(["a", "c"])
    _item(conn, item_id=1, concept_id="a")
    _item(conn, item_id=2, concept_id="c")
    for _ in range(5):  # у c неопределённость падает ниже порога
        beta.update(conn, "c", correct=1.0, now=0.0, settings=settings)

    question = next_question(conn, graph, now=0.0, settings=settings)

    assert question is not None
    assert question.concept_id == "a"


def test_skips_node_without_item(conn, settings) -> None:
    """Важный узел без задания банка пропускаем — спрашиваем следующий."""
    for cid in ("a", "b", "z"):
        _concept(conn, cid)
    graph = _graph(["a", "b", "z"], [("a", "z")])  # a важнее (есть зависимый z)
    _item(conn, item_id=1, concept_id="b")

    question = next_question(conn, graph, now=0.0, settings=settings)

    assert question is not None
    assert question.concept_id == "b"


def test_stops_when_uncertainty_below_threshold(conn, settings) -> None:
    _concept(conn, "a")
    graph = _graph(["a"])
    _item(conn, item_id=1, concept_id="a")
    for _ in range(10):
        beta.update(conn, "a", correct=1.0, now=0.0, settings=settings)

    assert next_question(conn, graph, now=0.0, settings=settings) is None


def test_stops_when_items_exhausted(conn, settings) -> None:
    _concept(conn, "a")
    graph = _graph(["a"])
    item = _item(conn, item_id=1, concept_id="a")

    result = next_question(
        conn, graph, asked_item_ids=frozenset({item.id}), now=0.0, settings=settings
    )

    assert result is None


def test_freshly_answered_item_is_not_asked_again(conn, settings) -> None:
    """При включённой паузе повторный вопрос сразу после ответа не задаётся."""
    cool = settings.model_copy(update={"item_repeat_cooldown_days": 1.0})
    _concept(conn, "a")
    graph = _graph(["a"])
    item = _item(conn, item_id=1, concept_id="a", answer_type="short", answer="read_csv")
    record_answer(
        conn,
        graph,
        DiagnosticQuestion(item=item, concept_id="a"),
        "read_csv",
        now=0.0,
        settings=cool,
    )

    soon = next_question(conn, graph, now=100.0, settings=cool)
    day_later = next_question(conn, graph, now=2 * beta.SECONDS_PER_DAY, settings=cool)

    assert soon is None  # кулдаун ещё идёт
    assert day_later is not None  # через сутки вопрос снова осмыслен


def test_rubric_item_without_criteria_is_not_offered(conn, settings) -> None:
    """Задание, которое нечем проверить, выдавать нельзя."""
    load_seed(conn)
    conn.execute("UPDATE criteria SET active = 0 WHERE rubric_id = 1")
    conn.commit()

    ids = {item.id for item in diagnostic._available_items(conn, include_rubric=True)}

    assert 9 not in ids  # рубрика 1 осталась без критериев
    assert 10 in ids


def test_open_item_is_not_used_for_diagnostics(conn, settings) -> None:
    """Рубричные задания диагностика не берёт — их проверяет LLM (Срез 6)."""
    _concept(conn, "a")
    graph = _graph(["a"])
    _item(conn, item_id=1, concept_id="a", answer_type="open", answer="эталон")

    assert next_question(conn, graph, now=0.0, settings=settings) is None


def test_prefers_item_closest_to_current_mastery(conn, settings) -> None:
    """Целевая сложность ≈ текущему владению — задание информативнее."""
    _concept(conn, "a")
    graph = _graph(["a"])
    _item(conn, item_id=1, concept_id="a", difficulty=0.9)
    _item(conn, item_id=2, concept_id="a", difficulty=0.5)

    question = next_question(conn, graph, now=0.0, settings=settings)

    assert question is not None
    assert question.item.id == 2  # априор 0.5 ближе к 0.5, чем 0.9


# --- запись ответа ---


def test_record_correct_answer_updates_mastery_and_event(conn, settings) -> None:
    _concept(conn, "a")
    graph = _graph(["a"])
    item = _item(conn, item_id=1, concept_id="a", answer_type="short", answer="read_csv")

    result = record_answer(
        conn,
        graph,
        DiagnosticQuestion(item=item, concept_id="a"),
        "  READ_CSV ",
        now=0.0,
        settings=settings,
    )

    assert result.score == 1.0
    assert beta.estimate(conn, "a", now=0.0, settings=settings).mean > 0.5
    (event,) = repos.get_events(conn, "a")
    assert (event.source, event.item_id, event.result) == ("checked", 1, 1.0)


def test_record_wrong_answer_lowers_mastery(conn, settings) -> None:
    _concept(conn, "a")
    graph = _graph(["a"])
    item = _item(conn, item_id=1, concept_id="a", answer_type="short", answer="read_csv")

    result = record_answer(
        conn, graph, DiagnosticQuestion(item=item, concept_id="a"), "read_excel",
        now=0.0, settings=settings,
    )

    assert result.score == 0.0
    assert beta.estimate(conn, "a", now=0.0, settings=settings).mean < 0.5


def test_success_propagates_to_prerequisites(conn, settings) -> None:
    for cid in ("a", "b"):
        _concept(conn, cid)
    graph = _graph(["a", "b"], [("a", "b")])
    item = _item(conn, item_id=1, concept_id="b")

    record_answer(
        conn, graph, DiagnosticQuestion(item=item, concept_id="b"), "0",
        now=0.0, settings=settings,
    )

    prereq = repos.get_mastery(conn, "a")
    assert prereq["alpha"] == pytest.approx(1.0 + settings.beta_propagate_weight)


def test_failure_does_not_propagate(conn, settings) -> None:
    for cid in ("a", "b"):
        _concept(conn, cid)
    graph = _graph(["a", "b"], [("a", "b")])
    item = _item(conn, item_id=1, concept_id="b")

    record_answer(
        conn, graph, DiagnosticQuestion(item=item, concept_id="b"), "1",
        now=0.0, settings=settings,
    )

    assert repos.get_mastery(conn, "a") is None


# --- длина захода ---


def test_first_pass_is_longer_than_followup(conn, settings) -> None:
    assert questions_for_pass(conn, settings=settings) == settings.diagnostic_first_pass

    repos.add_event(conn, Event(source="checked", result=1.0, ts=0.0))

    assert questions_for_pass(conn, settings=settings) == settings.diagnostic_followup


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
