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


def test_every_module_has_short_items(conn) -> None:
    """Типы заданий как в topic01: не только choice, но и short (§6.2, аудит 5-M3)."""
    load_course(conn)
    graph = CourseGraph.load(conn)

    short_topics = {
        graph.topic_of(max(item.concept_weights, key=item.concept_weights.get))
        for item in repos.get_items(conn)
        if item.answer_type == "short"
    }

    assert short_topics == set(graph.topic_ids)


# Дистракторы, которые придумывались как ложные, а запуск кода показал, что
# они верны (problems.md, раздел 4). Ученик, знающий sklearn, получал за них 0.
TRUE_DISTRACTORS = {
    4037: "Модель перестаёт зависеть от значений входных признаков",
    5013: "Чтобы ускорить обучение за счёт меньшего числа признаков",
    5029: "Деревья обучают не на бутстрэп-выборках, а на всей выборке",
    5044: "В сумме дают сто процентов по каждому дереву леса",
}


def test_distractors_proven_true_are_gone(conn) -> None:
    load_course(conn)
    items = {item.id: item for item in repos.get_items(conn)}

    for item_id, statement in TRUE_DISTRACTORS.items():
        assert statement not in items[item_id].options, item_id


def test_module4_content_audit(conn) -> None:
    """Аудит модуля 4: ребро, анкета и угадываемое задание (4-M1, 4-M2, 4-M3)."""
    course = load_course(conn)
    survey = next(topic.survey for topic in course.topics if topic.number == 4)
    items = {item.id: item for item in repos.get_items(conn)}

    # 4-M1: кейсы деревьев и kNN не пререквизит текстового пайплайна.
    assert ("trees_knn_cases", "text_pipeline") not in {
        (edge.from_id, edge.to_id) for edge in course.edges
    }
    # 4-M2: «уверенно обучаю в sklearn» не означает знания теории МНК.
    assumed = {
        concept
        for key in survey.assumed_by_confident
        for concept in survey.block(key).concepts
    }
    assert not assumed & {"gauss_markov", "normal_equation"}
    # 4-M3: short с подсказкой «L1 или L2» угадывался 50/50.
    assert items[4021].answer_type == "choice"
    # Взамен short модулю 4 — 4035 (C = 1/λ), иначе их бы не осталось ни одного.
    assert items[4035].answer_type == "short"
