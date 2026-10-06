"""Тесты планировщика: готовность, приоритет, режимы (Срез 4.4)."""

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Event
from llm_tutor.student import beta
from llm_tutor.student.planner import _importance, mode_for_node, ready_nodes


def _graph(concepts: list[str], edges: list[tuple[str, str, bool]]) -> CourseGraph:
    return CourseGraph(
        [Concept(id=cid, name=cid) for cid in concepts],
        [Edge(from_id=src, to_id=dst, hard=hard) for src, dst, hard in edges],
    )


def _set_mastery(
    conn, concept_id: str, mean: float, *, total: float = 20.0, next_review: float | None = None
) -> None:
    """Выставляет владение с заданным средним (last_seen=0 → decay не влияет)."""
    repos.upsert_concept(conn, Concept(id=concept_id, name=concept_id))
    repos.upsert_mastery(
        conn,
        concept_id,
        alpha=mean * total,
        beta=(1.0 - mean) * total,
        last_seen=0.0,
        next_review=next_review,
    )


def _modes(result) -> dict[str, str]:
    return {node.concept_id: node.mode for node in result}


# --- блокировка жёстким пререквизитом ---


def test_node_blocked_by_unmastered_hard_prereq(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b", True)])

    result = ready_nodes(conn, graph, now=0.0, settings=settings)

    assert [node.concept_id for node in result] == ["a"]  # b заблокирован


def test_node_ready_once_hard_prereq_mastered(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b", True)])
    _set_mastery(conn, "a", 0.8)

    ids = [node.concept_id for node in ready_nodes(conn, graph, now=0.0, settings=settings)]

    assert "b" in ids


def test_soft_prereq_does_not_block(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b", False)])

    ids = [node.concept_id for node in ready_nodes(conn, graph, now=0.0, settings=settings)]

    assert "b" in ids  # мягкий пререквизит не блокирует


# --- порядок по приоритету ---


def test_more_dependents_sorts_first(conn, settings) -> None:
    graph = _graph(["a", "b", "c"], [("a", "c", True)])

    result = ready_nodes(conn, graph, now=0.0, settings=settings)

    assert result[0].concept_id == "a"  # у a есть зависимый узел
    assert result[0].priority == pytest.approx(1.0)
    assert all(0.0 <= node.priority <= 1.0 for node in result)


# --- маппинг режимов ---


def test_cold_start_node_is_compressed(conn, settings) -> None:
    graph = _graph(["a"], [])

    assert _modes(ready_nodes(conn, graph, now=0.0, settings=settings)) == {"a": "compressed"}


def test_low_mastery_is_full(conn, settings) -> None:
    graph = _graph(["a"], [])
    _set_mastery(conn, "a", 0.2)

    assert _modes(ready_nodes(conn, graph, now=0.0, settings=settings)) == {"a": "full"}


def test_mid_high_mastery_is_verify(conn, settings) -> None:
    graph = _graph(["a"], [])
    _set_mastery(conn, "a", 0.8)

    assert _modes(ready_nodes(conn, graph, now=0.0, settings=settings)) == {"a": "verify"}


def test_weak_node_with_weak_soft_prereq_is_revisit(conn, settings) -> None:
    graph = _graph(["s", "t"], [("s", "t", False)])
    _set_mastery(conn, "s", 0.5)
    _set_mastery(conn, "t", 0.2)

    assert _modes(ready_nodes(conn, graph, now=0.0, settings=settings))["t"] == "revisit"


def test_overdue_node_is_review(conn, settings) -> None:
    graph = _graph(["a"], [])
    _set_mastery(conn, "a", 0.8, next_review=-10_000.0)

    assert _modes(ready_nodes(conn, graph, now=0.0, settings=settings)) == {"a": "review"}


def test_repeated_recent_failures_are_reinforce(conn, settings) -> None:
    graph = _graph(["a"], [])
    _set_mastery(conn, "a", 0.8)
    for _ in range(3):
        repos.add_event(conn, Event(source="checked", result=0.0, concept_id="a", ts=-1.0))

    assert _modes(ready_nodes(conn, graph, now=0.0, settings=settings)) == {"a": "reinforce"}


def test_confidently_mastered_node_is_skipped(conn, settings) -> None:
    graph = _graph(["a", "b"], [("a", "b", True)])
    _set_mastery(conn, "a", 0.95, total=40.0)  # малая σ → режим skip

    ids = [node.concept_id for node in ready_nodes(conn, graph, now=0.0, settings=settings)]

    assert ids == ["b"]  # a освоен уверенно и в маршрут не попадает


# --- реальный seed-граф ---


def test_cold_start_offers_only_baseline_nodes(conn, settings) -> None:
    """На холодном старте маршрут — только узлы без жёстких пререквизитов.

    Иначе ученик с порога получает `read_csv`/`sorting`/`agg_functions` в
    обход базы, ради которой весь граф и строился.
    """
    load_seed(conn)
    graph = CourseGraph.load(conn)

    ids = {
        node.concept_id
        for node in ready_nodes(conn, graph, limit=100, now=0.0, settings=settings)
    }

    roots = {n for n in graph.node_ids if not graph.hard_prerequisites(n)}
    assert ids == roots
    assert not ids & {"read_csv", "sorting", "agg_functions", "describe_stats"}


def test_goal_proximity_raises_importance() -> None:
    """Слагаемое «близость к цели» — реальный градиент, а не константа."""
    near = _importance(descendants=1, max_descendants=3, distance=1, max_distance=3)
    far = _importance(descendants=1, max_descendants=3, distance=3, max_distance=3)

    assert near > far


def test_review_mode_reachable_after_scheduled_interval(conn, settings) -> None:
    """Расписание из beta.update делает режим «повторение» достижимым в проде."""
    graph = _graph(["a"], [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    beta.update(conn, "a", correct=1.0, weight=4.0, now=0.0, settings=settings)

    due = repos.get_mastery(conn, "a")["next_review"]
    nodes = ready_nodes(conn, graph, now=due + 1.0, settings=settings)

    assert _modes(nodes) == {"a": "review"}


# --- цель и лимит ---


def test_goal_restricts_scope_to_ancestors(conn, settings) -> None:
    graph = _graph(["a", "b", "c"], [("a", "b", True), ("b", "c", True)])
    _set_mastery(conn, "a", 0.8)
    _set_mastery(conn, "b", 0.8)

    everywhere = {
        node.concept_id for node in ready_nodes(conn, graph, now=0.0, settings=settings)
    }
    to_b = {
        node.concept_id
        for node in ready_nodes(conn, graph, goal_concept_id="b", now=0.0, settings=settings)
    }

    assert "c" in everywhere  # без цели узел достижим
    assert "c" not in to_b  # цель b отсекает зависимый c


def test_unknown_goal_raises(conn, settings) -> None:
    graph = _graph(["a"], [])

    with pytest.raises(KeyError):
        ready_nodes(conn, graph, goal_concept_id="ghost", now=0.0, settings=settings)


def test_limit_caps_result_and_zero_is_empty(conn, settings) -> None:
    graph = _graph(["a", "b", "c"], [])

    assert len(ready_nodes(conn, graph, limit=2, now=0.0, settings=settings)) == 2
    assert ready_nodes(conn, graph, limit=0, now=0.0, settings=settings) == []


# --- режим узла как отдельная функция (Срез 9) ---


def test_mode_for_node_matches_ready_nodes(conn, settings) -> None:
    """Режим узла считается одинаково в маршруте и в списке готовых."""
    graph = _graph(["a", "b"], [("a", "b", True)])
    _set_mastery(conn, "a", 0.8, total=1.0)

    nodes = {
        node.concept_id: node.mode
        for node in ready_nodes(conn, graph, now=0.0, settings=settings)
    }

    for concept_id, mode in nodes.items():
        assert mode_for_node(conn, graph, concept_id, now=0.0, settings=settings) == mode


def test_mode_for_node_is_skip_for_confident_mastery(conn, settings) -> None:
    graph = _graph(["a"], [])
    _set_mastery(conn, "a", 0.95, total=40.0)

    assert mode_for_node(conn, graph, "a", now=0.0, settings=settings) == "skip"
