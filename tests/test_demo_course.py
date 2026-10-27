"""Демонстрационный мини-курс «Деревья решений и случайный лес»: один модуль."""

import json
from pathlib import Path

import pytest

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import DATA_DIR, load_course_data
from llm_tutor.student import survey
from llm_tutor.web import courses
from llm_tutor.web.userdb import UserDBPool
from web_fakes import ScriptedTutor

DEMO = "trees-demo"
LESSONS = ["tree_criteria", "tree_building", "tree_tuning", "bagging_basics", "forest"]
BASE = f"/api/courses/{DEMO}/lesson"


def _demo():
    course = courses.get_course(DEMO)
    assert course is not None, "курс trees-demo не зарегистрирован"
    return course


def test_registered_published_and_visible() -> None:
    course = _demo()
    assert course.status == "published" and course.visible
    assert course.root is None and course.data_dir == DATA_DIR / "demo"
    assert course in courses.published_courses()
    assert course.title and course.subtitle and course.description


def test_one_module_of_five_lessons_without_warnings() -> None:
    data = courses.course_data(_demo())
    assert [topic.number for topic in data.topics] == [1]
    assert data.topics[0].published
    assert [node.id for node in data.nodes] == LESSONS
    assert data.warnings == ()
    assert data.legacy == {}


def test_lessons_are_closed_under_prerequisites() -> None:
    data = courses.course_data(_demo())
    graph = CourseGraph(data.nodes, data.edges)
    # Все занятия достижимы и идут в понятном порядке: от критериев к лесу.
    order = graph.topo_order()
    assert order[0] == "tree_criteria" and order[-1] == "forest"
    assert order.index("tree_tuning") < order.index("bagging_basics")


def test_important_lessons_keep_explain_reference() -> None:
    data = courses.course_data(_demo())
    important = {node.id for node in data.nodes if node.explain is not None}
    assert important == {"tree_criteria", "bagging_basics", "forest"}


def test_every_lesson_has_theory_and_items() -> None:
    data = courses.course_data(_demo())
    assert set(data.theory) == set(LESSONS)
    for node in data.nodes:
        assert any(node.id in item.concept_weights for item in data.items), node.id
        assert any(item.final and node.id in item.concept_weights for item in data.items), node.id


def test_items_are_copies_of_the_main_course() -> None:
    """Задания не переписаны: те же условия, ответы и проверки, что в mlcourse (там они тестируются)."""
    main = {item.id: item for item in load_course_data().items}
    demo = courses.course_data(_demo()).items
    assert demo
    for item in demo:
        source = main[item.id]
        for field in ("prompt", "answer_type", "options", "answer", "starter", "setup", "tests", "section", "final"):
            assert getattr(item, field) == getattr(source, field), (item.id, field)
        # Веса — только по занятиям мини-курса.
        assert set(item.concept_weights) <= set(LESSONS)


def test_survey_covers_exactly_the_lessons() -> None:
    topic = courses.course_data(_demo()).topics[0]
    covered = [concept for block in topic.survey.blocks for concept in block.concepts]
    assert sorted(covered) == sorted(LESSONS)


def test_theory_does_not_point_to_missing_lessons() -> None:
    folder = DATA_DIR / "demo" / "theory" / "topic01"
    text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.md")))
    for phrase in ("следующих модулей", "(следующие занятия)"):
        assert phrase not in text


def test_own_materials_file(tmp_path) -> None:
    """Материалы RAG не общие с mlcourse: у них номера модулей совпали бы."""
    pool = UserDBPool(tmp_path / "users", tmp_path / "shared.sqlite3")
    assert pool.materials_path_for(DEMO) == DATA_DIR / "demo" / "materials.sqlite3"
    assert pool.materials_path_for("mlcourse") == tmp_path / "shared.sqlite3"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Дерево — это цепочка вопросов.\n---\nВторая часть.")


async def test_catalog_lists_three_courses(student) -> None:
    response = await student.get("/api/courses")
    assert response.status_code == 200
    ids = [card["id"] for card in response.json()["courses"]]
    assert ids == ["mlcourse", "isit-ml", DEMO]


async def test_enroll_survey_and_first_lesson(student) -> None:
    assert (await student.post(f"/api/courses/{DEMO}/enroll", json={})).status_code == 200
    state = (await student.get(f"{BASE}/state")).json()["state"]
    assert state["survey"] is True
    config = courses.course_data(_demo()).topics[0].survey
    progress, answers = survey.Progress(config), []
    while progress.next_key() is not None:
        progress, answers = progress.answer(0), [*answers, 0]

    finished = await student.post(f"{BASE}/survey/finish", json={"answers": answers})

    assert finished.status_code == 200, finished.text
    after = finished.json()["state"]
    assert after["survey"] is False
    assert after["node"]["id"] == "tree_criteria"
    assert after["node"]["lesson"]["module"]["title"] == "Деревья решений и случайный лес"
    assert (await student.get(f"/lesson/{DEMO}")).status_code == 200
