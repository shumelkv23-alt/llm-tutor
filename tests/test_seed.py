"""Тесты банка заданий и графа темы (Срез 20).

Банк — условие ведомого урока: «пара тестов» и закрытие по серии достижимы
только там, где у узла есть хотя бы два задания с автопроверкой.
"""

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import diagnostic


def test_every_node_has_two_auto_checked_items(conn, settings) -> None:
    """«Пара тестов» возможна на каждом узле: минимум два задания с автопроверкой."""
    load_seed(conn)
    graph = CourseGraph.load(conn)
    auto = diagnostic.AUTO_CHECKABLE

    for node_id in graph.node_ids:
        items = [
            item
            for item in repos.get_items(conn)
            if item.answer_type in auto and node_id in item.concept_weights
        ]
        assert len(items) >= 2, f"у узла {node_id} меньше двух заданий"


def test_choice_items_have_answer_among_options(conn, settings) -> None:
    """Варианты однозначны: верный есть, он один и не дублируется."""
    load_seed(conn)

    for item in repos.get_items(conn):
        if item.answer_type == "choice":
            assert len(item.options) >= 3, item.id
            assert len(set(item.options)) == len(item.options), item.id
            assert item.options[int(item.answer)], item.id


def test_short_items_have_reference_answer(conn, settings) -> None:
    """У short-задания есть непустой эталон — иначе проверить нечем."""
    load_seed(conn)

    for item in repos.get_items(conn):
        if item.answer_type == "short":
            assert (item.answer or "").strip(), item.id
