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


def test_topic01_order_is_kept_in_whole_course(conn) -> None:
    """Порядок модуля 1 не сдвигают рёбра других модулей (аудит среза 24, M2)."""
    from course_fixtures import TOPIC01_ORDER

    load_course(conn)

    assert CourseGraph.load(conn).topic_nodes(1) == TOPIC01_ORDER


# Модули 1 и 2 написаны до того, как появилось правило про длину вариантов: у них
# верный вариант choice систематически самый длинный (53% и 79%). Долг описан в
# problems.md и чинится отдельно; новый модуль в этот список попадать не должен.
LENGTH_DEBT_TOPICS = frozenset({1, 2})

MAX_LONGEST_ANSWER_SHARE = 0.25
"""Верхняя граница доли заданий, где верный вариант — самый длинный.

0.25 — это ровно вероятность случайного угадывания при четырёх вариантах: выше
неё «выбирай самый длинный» начинает обыгрывать честный ответ.
"""


def test_choice_answer_is_not_systematically_the_longest(conn) -> None:
    """Верный вариант не выдаёт себя длиной (правило контента §6.2)."""
    load_course(conn)
    graph = CourseGraph.load(conn)

    longest_by_topic: dict[int, list[bool]] = {}
    for item in repos.get_items(conn):
        if item.answer_type != "choice":
            continue
        main = max(item.concept_weights, key=item.concept_weights.get)
        lengths = [len(option) for option in item.options]
        answer_len = lengths[int(item.answer)]
        longest_by_topic.setdefault(graph.topic_of(main), []).append(
            answer_len == max(lengths) and lengths.count(max(lengths)) == 1
        )

    for topic_id, longest in sorted(longest_by_topic.items()):
        if topic_id in LENGTH_DEBT_TOPICS:
            continue
        share = sum(longest) / len(longest)
        assert share <= MAX_LONGEST_ANSWER_SHARE, (
            f"в модуле {topic_id} верный вариант — самый длинный "
            f"в {share:.0%} заданий ({sum(longest)} из {len(longest)})"
        )
