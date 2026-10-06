"""Тесты графа курса и загрузки seed (Срез 4.1–4.2)."""

import json

import pytest

from llm_tutor.course.graph import CourseGraph, CourseGraphError
from llm_tutor.course.seed import DEFAULT_SEED_PATH, load_seed, load_seed_data
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Event


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


def test_duplicate_concepts_are_rejected() -> None:
    with pytest.raises(CourseGraphError, match="Дубль концепта"):
        CourseGraph([Concept(id="a", name="x"), Concept(id="a", name="y")], [])


def test_duplicate_edges_are_rejected() -> None:
    with pytest.raises(CourseGraphError, match="Дубль ребра"):
        _graph(["a", "b"], [("a", "b", True), ("a", "b", False)])


def test_non_requires_edge_does_not_shadow_requires() -> None:
    """`part_of` между той же парой не должен вытеснять `requires` из графа."""
    graph = CourseGraph(
        [Concept(id="a", name="a"), Concept(id="b", name="b")],
        [
            Edge(from_id="a", to_id="b", type="part_of"),
            Edge(from_id="a", to_id="b", type="requires"),
        ],
    )

    assert graph.prerequisites("b") == ["a"]
    assert graph.edge_weight("a", "b") == 1.0


def test_cycle_of_non_requires_edges_does_not_block_graph() -> None:
    """Цикл из `part_of` — не цикл пререквизитов: граф строится."""
    graph = CourseGraph(
        [Concept(id="a", name="a"), Concept(id="b", name="b")],
        [
            Edge(from_id="a", to_id="b", type="part_of"),
            Edge(from_id="b", to_id="a", type="part_of"),
        ],
    )

    assert graph.prerequisites("b") == []
    assert graph.topo_order()  # порядок определён


def test_distance_between_nodes() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b", True), ("b", "c", True)])

    assert graph.distance("a", "c") == 2
    assert graph.distance("b", "b") == 0
    assert graph.distance("c", "a") is None


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


def _write_trimmed_seed(tmp_path, *, drop_nodes=(), drop_edges=()) -> "Path":
    """Пишет копию seed без указанных узлов/рёбер (для проверки реконсиляции)."""
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    data["nodes"] = [n for n in data["nodes"] if n["id"] not in drop_nodes]
    data["edges"] = [
        e
        for e in data["edges"]
        if (e["from_id"], e["to_id"]) not in drop_edges
        and e["from_id"] not in drop_nodes
        and e["to_id"] not in drop_nodes
    ]
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_seed_prunes_stale_edges(conn, tmp_path) -> None:
    """Ребро, убранное из seed, не остаётся в БД призраком."""
    load_seed(conn)
    path = _write_trimmed_seed(tmp_path, drop_edges={("python_basics", "numpy_basics")})

    load_seed(conn, path)

    assert CourseGraph.load(conn).hard_prerequisites("numpy_basics") == []


def test_load_seed_prunes_stale_concepts(conn, tmp_path) -> None:
    load_seed(conn)
    path = _write_trimmed_seed(tmp_path, drop_nodes={"sorting"})

    load_seed(conn, path)

    assert "sorting" not in CourseGraph.load(conn).node_ids


def test_load_seed_refuses_to_prune_concept_with_events(conn, tmp_path) -> None:
    """Узел со ссылками в журнале событий не удаляется — операция откатывается."""
    load_seed(conn)
    repos.add_event(conn, Event(source="checked", result=1.0, concept_id="sorting", ts=1.0))
    path = _write_trimmed_seed(tmp_path, drop_nodes={"sorting"})

    with pytest.raises(RuntimeError, match="данные ученика"):
        load_seed(conn, path)

    assert "sorting" in CourseGraph.load(conn).node_ids


def test_seed_cli_missing_file_reports_clearly(tmp_path) -> None:
    from llm_tutor.course.seed import main

    with pytest.raises(FileNotFoundError, match="не найден"):
        main(["--seed", str(tmp_path / "nope.json"), "--db", str(tmp_path / "t.db")])


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
