"""Реальные seed-файлы курса: все модули грузятся и годятся для урока (срез 24).

С среза 56 курс — 33 занятия с разделами. Модули 2–5 пока не опубликованы и
в граф ученика (``CourseGraph.load``) не входят, поэтому граф здесь строится
из данных курса целиком.
"""

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course, load_course_data
from llm_tutor.db import repos
from llm_tutor.student import diagnostic


def _graph() -> CourseGraph:
    """Граф всего курса, включая неопубликованные модули."""
    course = load_course_data()
    return CourseGraph(course.nodes, course.edges)


# Порядок занятий в модулях — как в таблицах плана dense-lessons §2.
LESSON_ORDER = {
    1: ["pandas_structures", "load_inspect", "types_stats", "selection", "transform",
        "grouping", "eda_case"],
    2: ["viz_intro", "distributions", "numeric_relations", "mixed_relations", "tsne_plotly",
        "visual_churn"],
    3: ["ml_basics", "tree_criteria", "tree_building", "tree_tuning", "knn", "validation",
        "trees_knn_practice"],
    4: ["linreg", "linreg_stats", "ridge", "logreg", "nonlinear", "text", "curves"],
    5: ["bagging_basics", "forest", "rf_theory", "rf_tuning", "rf_properties",
        "feature_importance"],
}


def test_modules_are_numbered_from_one_without_gaps(conn) -> None:
    numbers = [topic.number for topic in load_course(conn).topics]

    assert numbers == list(range(1, len(numbers) + 1))


def test_lessons_and_sections_per_module() -> None:
    """33 занятия из 96 разделов: 7, 6, 7, 7, 6 занятий (план §2, сводка)."""
    course = load_course_data()
    lessons = {n: [node for node in course.nodes if node.topic_id == n] for n in range(1, 6)}

    assert [len(lessons[n]) for n in range(1, 6)] == [7, 6, 7, 7, 6]
    assert [sum(len(node.sections) for node in lessons[n]) for n in range(1, 6)] == [
        20, 18, 20, 18, 20
    ]


def test_lesson_order_in_every_module_follows_the_plan() -> None:
    graph = _graph()

    for number, expected in LESSON_ORDER.items():
        assert graph.topic_nodes(number) == expected, number


def test_python_and_numpy_are_gone() -> None:
    course = load_course_data()
    ids = {node.id for node in course.nodes}

    assert not ids & {"python_basics", "numpy_basics"}
    assert course.legacy["python_basics"] is None and course.legacy["numpy_basics"] is None
    assert "block_python" not in course.topics[0].survey.keys
    assert course.topics[0].survey.foundation == ("block_tables",)
    assert "Python" not in course.topics[0].survey.level_question


def test_legacy_covers_all_103_old_topics() -> None:
    from legacy_course import load_course_data as load_old

    old = {node.id for node in load_old().nodes}
    course = load_course_data()

    assert set(course.legacy) == old
    assert course.legacy["groupby"].node == "grouping"
    assert course.legacy["visualization_basics"].node == "viz_intro"


def test_modules_are_published_in_order() -> None:
    """Модули открываются по порядку срезов 57–61; остальные — «скоро» (срез 59: 1–3)."""
    assert [topic.published for topic in load_course_data().topics] == [
        True, True, True, False, False
    ]


def test_every_node_has_two_auto_checked_items(conn) -> None:
    load_course(conn)
    graph = _graph()
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


def test_every_choice_option_is_graded_by_its_own_text(conn) -> None:
    """Кнопка с вариантом засчитывается ровно как этот вариант (F-1: «150»)."""
    from llm_tutor.core.turn import _normalize_choice_answer
    from llm_tutor.grader import autocheck

    load_course(conn)

    for item in repos.get_items(conn):
        if item.answer_type != "choice":
            continue
        for index, option in enumerate(item.options):
            given = _normalize_choice_answer(item, option)
            expected = 1.0 if index == int(item.answer) else 0.0
            assert autocheck.check(item, given).score == expected, (item.id, option)


def test_reference_answer_typed_as_text_is_answer_and_passes(conn) -> None:
    """Эталон, набранный текстом, — ответ, а не реплика, и проходит автопроверку (F-8)."""
    from course_fixtures import correct_answer

    from llm_tutor.core import guided
    from llm_tutor.core.turn import _normalize_choice_answer
    from llm_tutor.grader import autocheck

    load_course(conn)

    for item in repos.get_items(conn):
        if item.answer_type not in ("choice", "short"):
            continue
        typed = correct_answer(item)
        assert guided.answer_kind(item, typed) == "answer", (item.id, typed)
        given = _normalize_choice_answer(item, typed) if item.answer_type == "choice" else typed
        assert autocheck.check(item, given).score == 1.0, (item.id, typed)


def test_items_lean_only_on_earlier_modules(conn) -> None:
    """Вторичный вес задания — на тему своего или прошлого модуля, не будущего."""
    load_course(conn)
    graph = _graph()

    for item in repos.get_items(conn):
        main = max(item.concept_weights, key=item.concept_weights.get)
        assert all(
            graph.topic_of(node) <= graph.topic_of(main) for node in item.concept_weights
        ), item.id


def test_module1_order_is_kept_in_whole_course(conn) -> None:
    """Порядок модуля 1 не сдвигают рёбра других модулей (аудит среза 24, M2)."""
    load_course(conn)

    assert CourseGraph.load(conn).topic_nodes(1) == LESSON_ORDER[1]


MAX_LONGEST_ANSWER_SHARE = 0.25
"""Верхняя граница доли заданий, где верный вариант — самый длинный.

0.25 — это ровно вероятность случайного угадывания при четырёх вариантах: выше
неё «выбирай самый длинный» начинает обыгрывать честный ответ.
"""


def test_choice_answer_is_not_systematically_the_longest(conn) -> None:
    """Верный вариант не выдаёт себя длиной (правило контента §6.2)."""
    load_course(conn)
    graph = _graph()

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
        share = sum(longest) / len(longest)
        assert share <= MAX_LONGEST_ANSWER_SHARE, (
            f"в модуле {topic_id} верный вариант — самый длинный "
            f"в {share:.0%} заданий ({sum(longest)} из {len(longest)})"
        )


def test_every_module_has_short_items(conn) -> None:
    """Типы заданий как в topic01: не только choice, но и short (§6.2, аудит 5-M3)."""
    load_course(conn)
    graph = _graph()

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
    assert ("trees_knn_practice", "text") not in {
        (edge.from_id, edge.to_id) for edge in course.edges
    }
    # 4-M2: «уверенно обучаю в sklearn» не означает знания теории МНК.
    assumed = {
        concept
        for key in survey.assumed_by_confident
        for concept in survey.block(key).concepts
    }
    assert not assumed & {"linreg_stats"}
    # 4-M3: short с подсказкой «L1 или L2» угадывался 50/50.
    assert items[4021].answer_type == "choice"
    # Взамен short модулю 4 — 4035 (C = 1/λ), иначе их бы не осталось ни одного.
    assert items[4035].answer_type == "short"
