"""Эталон «Объясни новичку» встроенного курса (срез 90)."""

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course_data


def test_all_published_modules_have_reference() -> None:
    course = load_course_data()
    for topic in course.topics:
        if topic.published:
            assert topic.explain is not None, topic.number
    assert not any("эталона" in warning for warning in course.warnings)


def test_ideas_cover_every_lesson() -> None:
    course = load_course_data()
    graph = CourseGraph(course.nodes, course.edges)
    for topic in course.topics:
        if topic.explain is None:
            continue
        lessons = {node for node in graph.topo_order() if graph.topic_of(node) == topic.number}
        covered = {idea.lesson for idea in topic.explain.ideas}
        assert lessons == covered, (topic.number, sorted(lessons - covered))


def test_beliefs_are_questions() -> None:
    # Загвоздка звучит от лица новичка — догадкой-вопросом, а не утверждением.
    for topic in load_course_data().topics:
        for pitfall in topic.explain.pitfalls if topic.explain else []:
            assert pitfall.belief.endswith("?"), pitfall.id
