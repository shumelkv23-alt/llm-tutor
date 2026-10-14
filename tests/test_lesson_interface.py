"""Интерфейс занятия (срез 55): состояние для страницы, «скоро», подписи, тексты."""

import pytest

from course_fixtures import correct_answer, load_lessons
from test_lesson_sections import _answer, _check, _enter, _pending, _step, _wrong
from test_turn import _state

from llm_tutor.core import intents
from llm_tutor.core.overview import module_rows, topic_rows
from llm_tutor.core.turn import MODULES_SOON_NOTE
from llm_tutor.course.graph import CourseGraph
from legacy_course import load_course_data
from llm_tutor.db import repos
from llm_tutor.web import accounts, views
from course_fixtures import LESSON_SEED_PATHS, use_lessons_course
from web_fakes import ScriptedTutor


def _unpublish(conn, number: int) -> None:
    course = load_course_data(LESSON_SEED_PATHS)
    topics = [
        topic.model_copy(update={"published": topic.number != number}) for topic in course.topics
    ]
    repos.replace_seed(
        conn, course.nodes, course.edges, course.items, theory=course.theory, topics=topics
    )


def _surveys_done(conn) -> None:
    """Анкеты обоих модулей пройдены — состояние занятия, а не анкеты."""
    from llm_tutor.student import survey

    for topic in repos.get_topics(conn):
        config = topic.survey
        survey.apply_answers(conn, config, {block.key: 0 for block in config.blocks}, now=0.5)


async def test_state_tells_module_lesson_and_section(conn, settings) -> None:
    load_lessons(conn)
    _surveys_done(conn)
    await _enter(conn, settings)

    lesson = views.lesson_state(conn, settings=settings)["node"]["lesson"]

    assert lesson["module"] == {"number": 1, "title": "Таблицы (тестовый модуль с занятиями)"}
    assert (lesson["index"], lesson["total"]) == (1, 3)
    assert [s["status"] for s in lesson["sections"]] == ["current", "ahead"]
    assert lesson["section_index"] == 1
    assert lesson["final"] == {"title": "Итоговое задание", "status": "ahead"}


async def test_state_marks_passed_weak_and_final(conn, settings) -> None:
    load_lessons(conn)
    _surveys_done(conn)
    await _enter(conn, settings)
    _check(conn, settings)
    await _answer(conn, settings, _wrong(_pending(conn)))
    await _answer(conn, settings, _wrong(_pending(conn)))  # Series — слабый
    _check(conn, settings)
    await _answer(conn, settings, correct_answer(_pending(conn)))  # DataFrame

    state = views.lesson_state(conn, settings=settings)
    lesson = state["node"]["lesson"]

    assert [s["status"] for s in lesson["sections"]] == ["weak", "passed"]
    assert lesson["section_index"] is None and lesson["final"]["status"] == "current"
    assert state["item"]["part"] == "Итоговое задание"


async def test_practice_item_carries_section_label(conn, settings) -> None:
    load_lessons(conn)
    _surveys_done(conn)
    await _enter(conn, settings)
    _check(conn, settings)

    item = views.lesson_state(conn, settings=settings)["item"]

    assert item["part"] == "Раздел 1: Series"


async def test_state_shows_parts_without_items_as_unverifiable(conn, settings) -> None:
    """Раздел без заданий и отсутствующее итоговое — «–», а не «впереди»."""
    load_lessons(conn)
    _surveys_done(conn)
    conn.execute("UPDATE items SET active = 0 WHERE id IN (3001, 3002, 3005)")
    conn.commit()
    await _enter(conn, settings)
    _check(conn, settings)

    lesson = views.lesson_state(conn, settings=settings)["node"]["lesson"]

    assert [s["status"] for s in lesson["sections"]] == ["skipped", "current"]
    assert lesson["final"]["status"] == "skipped"


async def test_verify_streak_target_counts_sections_and_final(conn, settings) -> None:
    from llm_tutor.core.verify import start_verification

    load_lessons(conn)
    _surveys_done(conn)
    await _enter(conn, settings)
    start_verification(conn, now=2.0, settings=settings)

    assert views.lesson_state(conn, settings=settings)["streak_target"] == 3


def test_unpublished_module_is_hidden_from_graph_and_marked_soon(conn, settings) -> None:
    load_lessons(conn)
    _unpublish(conn, 2)
    _surveys_done(conn)

    graph = CourseGraph.load(conn)
    rows = module_rows(conn, topic_rows(conn, settings=settings))

    assert not any(node.startswith("l2_") for node in graph.node_ids)
    assert [(m.number, m.published, m.total) for m in rows] == [(1, True, 3), (2, False, 0)]
    modules = views.lesson_state(conn, settings=settings)["modules"]
    assert modules[1]["published"] is False


async def test_finishing_last_open_module_says_next_ones_are_coming(conn, settings) -> None:
    load_lessons(conn)
    _unpublish(conn, 2)
    await _enter(conn, settings)
    reply = None
    for node in ("l1_frames", "l1_select", "l1_group"):
        assert _state(conn).current_node_id == node
        for _ in range(2):
            _check(conn, settings)
            await _answer(conn, settings, correct_answer(_pending(conn)))
        reply = await _answer(conn, settings, correct_answer(_pending(conn)))

    assert MODULES_SOON_NOTE in reply.text
    assert "курс пройден" not in reply.text
    assert _state(conn).current_node_id is None
    assert _step(conn, "l1_group").status == "closed"


async def test_switch_into_unpublished_module_is_not_found(conn, settings) -> None:
    load_lessons(conn)
    _unpublish(conn, 2)

    assert not CourseGraph.load(conn).has_node("l2_hist")


def test_close_lesson_phrases_are_intents() -> None:
    assert intents.detect("закрой занятие") == "close_topic"
    assert intents.detect("закрой тему") == "close_topic"


@pytest.fixture
def web_llm():
    return ScriptedTutor("A\n---\nB")


@pytest.fixture
async def lessons_student(monkeypatch, student):
    use_lessons_course(monkeypatch)
    response = await student.post("/api/courses/mlcourse/enroll", json={})
    assert response.status_code == 200
    return student


BASE = "/api/courses/mlcourse/lesson"


async def test_web_state_and_page_show_lesson_parts(lessons_student, web_app) -> None:
    from llm_tutor.student import survey

    user = web_app.state.accounts
    config = load_course_data(LESSON_SEED_PATHS).topics[0].survey
    progress, answers = survey.Progress(config), []
    while (key := progress.next_key()) is not None:
        progress, answers = progress.answer(0), [*answers, 0]
    finished = await lessons_student.post(f"{BASE}/survey/finish", json={"answers": answers})
    assert finished.status_code == 200, finished.text
    state = finished.json()["state"]

    assert state["node"]["lesson"]["module"]["number"] == 1
    assert state["node"]["lesson"]["sections"][0]["title"] == "Series"
    html = (await lessons_student.get("/lesson/mlcourse")).text
    assert 'id="section-strip"' in html and "Закрыть занятие" in html
    home = (await lessons_student.get("/")).text
    assert "Модуль 1" in home and "Модуль 2" in home
    assert user is not None


async def test_web_switch_into_unpublished_module_is_refused(lessons_student, web_app) -> None:
    conn = web_app.state.userdbs.open(
        accounts.find_user(web_app.state.accounts, "ann@example.com").id, "mlcourse"
    )
    _unpublish(conn, 2)
    from llm_tutor.student import survey

    config = load_course_data(LESSON_SEED_PATHS).topics[0].survey
    progress, answers = survey.Progress(config), []
    while (key := progress.next_key()) is not None:
        progress, answers = progress.answer(0), [*answers, 0]
    await lessons_student.post(f"{BASE}/survey/finish", json={"answers": answers})

    response = await lessons_student.post(f"{BASE}/switch", json={"node_id": "l2_hist"})

    assert response.status_code == 404
    state = (await lessons_student.get(f"{BASE}/state")).json()["state"]
    assert state["modules"][1]["published"] is False


def test_lesson_state_without_sections_has_empty_lesson_parts(conn, settings) -> None:
    from legacy_course import load_seed

    load_seed(conn)
    graph = CourseGraph.load(conn)
    node = graph.topo_order()[0]
    view = views.lesson_view(conn, graph, _state(conn), node)
    assert view["sections"] == [] and view["final"] is None
    assert view["module"]["number"] == 1 and view["index"] == 1

