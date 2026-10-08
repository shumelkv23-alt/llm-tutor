"""Реальные seed-файлы курса: все модули грузятся и годятся для урока (срез 24)."""

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course
from llm_tutor.db import repos
from llm_tutor.student import diagnostic


def test_modules_are_numbered_from_one_without_gaps(conn) -> None:
    numbers = [topic.number for topic in load_course(conn).topics]

    assert numbers == list(range(1, len(numbers) + 1))


def test_every_module_has_15_to_25_topics(conn) -> None:
    load_course(conn)
    graph = CourseGraph.load(conn)

    for topic_id in graph.topic_ids:
        assert 15 <= len(graph.topic_nodes(topic_id)) <= 25, topic_id


def test_every_node_has_two_auto_checked_items(conn) -> None:
    load_course(conn)
    graph = CourseGraph.load(conn)
    items = repos.get_items(conn)

    for node_id in graph.node_ids:
        auto = [
            item
            for item in items
            if item.answer_type in diagnostic.AUTO_CHECKABLE and node_id in item.concept_weights
        ]
        assert len(auto) >= 2, f"у темы {node_id} меньше двух заданий с автопроверкой"


def test_choice_items_are_unambiguous(conn) -> None:
    load_course(conn)

    for item in repos.get_items(conn):
        if item.answer_type == "choice":
            assert 3 <= len(item.options) <= 5, item.id
            assert len(set(item.options)) == len(item.options), item.id


def test_items_lean_only_on_earlier_modules(conn) -> None:
    """Вторичный вес задания — на тему своего или прошлого модуля, не будущего."""
    load_course(conn)
    graph = CourseGraph.load(conn)

    for item in repos.get_items(conn):
        main = max(item.concept_weights, key=item.concept_weights.get)
        assert all(
            graph.topic_of(node) <= graph.topic_of(main) for node in item.concept_weights
        ), item.id
