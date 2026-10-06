"""Тесты графа курса и загрузки seed (Срез 4.1–4.2)."""

import pytest

from llm_tutor.course.graph import CourseGraph, CourseGraphError
from llm_tutor.course.seed import DEFAULT_SEED_PATH, load_seed, load_seed_data
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge


def _graph(concepts: list[str], edges: list[tuple[str, str, bool]]) -> CourseGraph:
    """Собирает граф из короткой нотации: id-шники и рёбра (from, to, hard)."""
    return CourseGraph(
        [Concept(id=cid, name=cid) for cid in concepts],
        [Edge(from_id=src, to_id=dst, hard=hard) for src, dst, hard in edges],
    )


# --- структура графа ---


def test_prerequisites_and_dependents_split_hard_soft() -> None:
    graph = _graph(["a", "b", "c"], [("a", "c", True), ("b", "c", False)])

    assert graph.prerequisites("c") == ["a", "b"]
    assert graph.hard_prerequisites("c") == ["a"]
    assert graph.soft_prerequisites("c") == ["b"]
    assert graph.dependents("a") == ["c"]
    assert graph.dependents("c") == []


def test_topo_order_places_prerequisites_first() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b", True), ("b", "c", True)])

    order = graph.topo_order()

    assert order.index("a") < order.index("b") < order.index("c")


def test_ancestors_and_descendants_are_transitive() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b", True), ("b", "c", True)])

    assert graph.ancestors("c") == {"a", "b"}
    assert graph.descendants("a") == {"b", "c"}


def test_edge_weight_returns_zero_for_missing_edge() -> None:
    graph = _graph(["a", "b"], [("a", "b", True)])

    assert graph.edge_weight("a", "b") == 1.0
    assert graph.edge_weight("b", "a") == 0.0


def test_concept_missing_raises_key_error() -> None:
    graph = _graph(["a"], [])

    with pytest.raises(KeyError):
        graph.concept("nope")


# --- валидация ---


def test_cycle_is_rejected() -> None:
    with pytest.raises(CourseGraphError):
        _graph(["a", "b"], [("a", "b", True), ("b", "a", True)])


def test_self_loop_is_rejected() -> None:
    with pytest.raises(CourseGraphError):
        _graph(["a"], [("a", "a", True)])


def test_edge_to_unknown_concept_is_rejected() -> None:
    with pytest.raises(CourseGraphError):
        _graph(["a"], [("a", "ghost", True)])


# --- seed ---


def test_real_seed_is_dag_with_required_size() -> None:
    seed = load_seed_data(DEFAULT_SEED_PATH)

    CourseGraph(seed.nodes, seed.edges)  # не падает — DAG без циклов
    assert 15 <= len(seed.nodes) <= 25


def test_load_seed_writes_nodes_and_edges(conn) -> None:
    seed = load_seed(conn)

    assert len(repos.get_concepts(conn)) == len(seed.nodes)
    assert len(repos.get_edges(conn)) == len(seed.edges)


def test_load_seed_is_idempotent(conn) -> None:
    load_seed(conn)
    nodes, edges = len(repos.get_concepts(conn)), len(repos.get_edges(conn))

    load_seed(conn)

    assert (len(repos.get_concepts(conn)), len(repos.get_edges(conn))) == (nodes, edges)


def test_seed_cli_loads_into_db(tmp_path, capsys) -> None:
    from llm_tutor.course.seed import main

    code = main(["--db", str(tmp_path / "t.db")])

    assert code == 0
    assert "концептов" in capsys.readouterr().out


def test_db_graph_matches_seed(conn) -> None:
    seed = load_seed(conn)

    graph = CourseGraph.load(conn)

    assert set(graph.node_ids) == {node.id for node in seed.nodes}
    assert graph.difficulty("groupby") == pytest.approx(
        next(n.difficulty for n in seed.nodes if n.id == "groupby")
    )
