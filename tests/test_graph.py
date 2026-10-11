"""Тесты графа курса и загрузки seed (Срез 4.1–4.2)."""

import json

import pytest
from course_fixtures import TOPIC01_ORDER

from llm_tutor.course.graph import CourseGraph, CourseGraphError
from llm_tutor.course.seed import (
    DEFAULT_SEED_PATH,
    SeedError,
    load_seed,
    items_without_rubric,
    load_seed_data,
    nodes_without_items,
)
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
    # Задания убранных узлов тоже уходят: ссылка на несуществующий концепт —
    # ошибка seed, а не «безобидный лишний пункт банка».
    data["items"] = [
        item
        for item in data["items"]
        if not set(item["concept_weights"]) & set(drop_nodes)
    ]
    # Анкета модуля накрывает ровно его темы (срез 24): убранная тема уходит и из блока.
    for block in data["topic"]["survey"]["blocks"]:
        block["concepts"] = [c for c in block["concepts"] if c not in drop_nodes]
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_seed_writes_items(conn) -> None:
    seed = load_seed(conn)

    stored = repos.get_items(conn)

    assert {item.id for item in stored} == {item.id for item in seed.items}
    assert repos.get_item(conn, stored[0].id) == stored[0]


def test_load_seed_deactivates_stale_items(conn, tmp_path) -> None:
    """Убранное из seed задание гаснет, а не удаляется (на него ссылается журнал)."""
    load_seed(conn)
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    data["items"] = [item for item in data["items"] if item["id"] != 5]
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    load_seed(conn, path)

    assert repos.get_item(conn, 5).active is False
    assert 5 not in {item.id for item in repos.get_items(conn)}


def test_load_seed_writes_rubrics_and_criteria(conn) -> None:
    seed = load_seed(conn)

    assert {rubric.id for rubric in repos.get_rubrics(conn)} == {
        rubric.id for rubric in seed.rubrics
    }
    first_rubric = seed.rubrics[0]
    expected = [c for c in seed.criteria if c.rubric_id == first_rubric.id]
    assert len(repos.get_criteria(conn, first_rubric.id)) == len(expected)


def test_load_seed_deactivates_stale_rubric(conn, tmp_path) -> None:
    """Убранная из seed рубрика гаснет, а не ломает загрузку."""
    load_seed(conn)
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    data["rubrics"] = [r for r in data["rubrics"] if r["id"] != 1]
    data["criteria"] = [c for c in data["criteria"] if c["rubric_id"] != 1]
    for item in data["items"]:
        if item.get("rubric_id") == 1:
            item["rubric_id"] = None
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    load_seed(conn, path)

    assert 1 not in {rubric.id for rubric in repos.get_rubrics(conn)}
    assert repos.get_criteria(conn, 1) == []


def test_get_item_missing_returns_none(conn) -> None:
    load_seed(conn)

    assert repos.get_item(conn, 999) is None


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


def test_load_seed_deactivates_concept_with_events(conn, tmp_path) -> None:
    """Узел со ссылками в журнале не удаляется и не роняет старт — гаснет."""
    load_seed(conn)
    repos.add_event(conn, Event(source="checked", result=1.0, concept_id="sorting", ts=1.0))
    path = _write_trimmed_seed(tmp_path, drop_nodes={"sorting"})

    load_seed(conn, path)  # не падает

    assert "sorting" not in CourseGraph.load(conn).node_ids  # узел ушёл из графа
    assert repos.get_events(conn, "sorting")  # но журнал цел


def test_nodes_without_items_reports_gate_nodes() -> None:
    """Несущий узел без задания запирал бы маршрут — о нём надо предупреждать.

    Сам seed дыр не имеет (срез 20: банк покрывает все узлы), поэтому случай
    собирается руками — иначе проверка перестала бы что-либо проверять.
    """
    seed = load_seed_data(DEFAULT_SEED_PATH)
    assert nodes_without_items(seed) == []

    def _without_items_for(node_id: str) -> object:
        return seed.model_copy(
            update={
                "items": [
                    item for item in seed.items if node_id not in item.concept_weights
                ]
            }
        )

    # Лист без зависимых не блокирует маршрут, поэтому в отчёт не попадает.
    assert nodes_without_items(_without_items_for("churn_eda_case")) == []
    assert nodes_without_items(_without_items_for("numpy_basics")) == ["numpy_basics"]


def test_seed_rejects_unknown_rubric_reference(tmp_path) -> None:
    """Опечатка в id рубрики — понятная ошибка автору, а не IntegrityError."""
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    for item in data["items"]:
        if item["id"] == 9:
            item["rubric_id"] = 99
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SeedError, match="рубрику"):
        load_seed_data(path)


def test_seed_rejects_unknown_concept_in_item(tmp_path) -> None:
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    data["items"][0]["concept_weights"] = {"ghost": 1.0}
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SeedError, match="концепты"):
        load_seed_data(path)


def test_seed_rejects_duplicate_item_ids(tmp_path) -> None:
    """Дубли id молча схлопнулись бы в upsert — содержимое разошлось бы с файлом."""
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    data["items"].append(dict(data["items"][0]))
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SeedError, match="Дубли id"):
        load_seed_data(path)


def test_open_item_without_rubric_is_reported(tmp_path) -> None:
    """Открытое задание без рубрики проверить нечем — о нём нужно сказать."""
    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    for item in data["items"]:
        if item["id"] == 9:
            item["rubric_id"] = None
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    seed = load_seed_data(path)

    assert items_without_rubric(seed) == [9]


def test_seed_without_open_items_reports_nothing() -> None:
    assert items_without_rubric(load_seed_data(DEFAULT_SEED_PATH)) == []


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


def _modules(nodes: dict[str, int], edges: list[tuple[str, str]]) -> CourseGraph:
    return CourseGraph(
        [Concept(id=node, name=node, topic_id=topic) for node, topic in nodes.items()],
        [Edge(from_id=src, to_id=dst, hard=True) for src, dst in edges],
    )


def test_topo_order_goes_module_by_module() -> None:
    graph = _modules({"b2": 2, "a1": 1, "c1": 1}, [("a1", "b2")])

    assert graph.topo_order()[-1] == "b2"
    # Без рёбер прежний порядок шёл бы по вставке узлов: модуль 2 впереди.
    assert _modules({"b2": 2, "a1": 1}, []).topo_order() == ["a1", "b2"]


def test_forward_edge_between_modules_is_rejected() -> None:
    with pytest.raises(CourseGraphError, match="вперёд"):
        _modules({"a1": 1, "b2": 2}, [("b2", "a1")])


def test_topic_helpers() -> None:
    graph = _modules({"a1": 1, "b2": 2, "c2": 2}, [("b2", "c2")])

    assert graph.topic_of("c2") == 2
    assert graph.topic_ids == [1, 2]
    assert graph.topic_nodes(2) == ["b2", "c2"]


def test_topic01_order_is_unchanged(conn) -> None:
    load_seed(conn)

    assert CourseGraph.load(conn).topo_order() == TOPIC01_ORDER
