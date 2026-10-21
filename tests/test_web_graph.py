"""Данные карты знаний в профиле (Срез 74)."""

import pytest
from course_fixtures import WEB_T1 as T1
from web_fakes import ScriptedTutor

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep
from llm_tutor.student import survey
from llm_tutor.web import accounts

COURSE = "mlcourse"
GRAPH = f"/api/courses/{COURSE}/graph"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю тему.")


@pytest.fixture
async def enrolled(student):
    assert (await student.post(f"/api/courses/{COURSE}/enroll", json={})).status_code == 200
    return student


def _db(web_app):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, COURSE)


def _answers() -> list[int]:
    progress, given = survey.Progress(T1), []
    while progress.next_key() is not None:
        progress, given = progress.answer(1), [*given, 1]
    return given


async def _finish_survey(client) -> None:
    response = await client.post(f"/api/courses/{COURSE}/lesson/survey/finish", json={"answers": _answers()})
    assert response.status_code == 200, response.text


async def test_graph_requires_enrollment(student, web) -> None:
    assert (await student.get(GRAPH)).status_code == 403
    assert (await student.get("/api/courses/nope/graph")).status_code == 404


async def test_graph_requires_login(web) -> None:
    assert (await web.get(GRAPH)).status_code == 401


async def test_graph_lists_nodes_and_edges_of_open_modules(enrolled, web_app) -> None:
    body = (await enrolled.get(GRAPH)).json()
    graph = CourseGraph.load(_db(web_app))

    ids = [node["id"] for node in body["nodes"]]
    assert ids == graph.topo_order()  # только открытые модули, порядок изучения
    edges = {(edge["from"], edge["to"]) for edge in body["edges"]}
    assert edges == {(a, b) for a, b, _ in graph.prerequisite_edges()}
    assert all(edge["from"] in ids and edge["to"] in ids for edge in body["edges"])
    assert {edge["hard"] for edge in body["edges"]} == {True, False}
    node = body["nodes"][0]
    assert set(node) == {"id", "name", "module", "status", "weak", "mastery", "confident", "needs_survey"}
    assert 0 <= node["mastery"] <= 100
    assert [module["number"] for module in body["modules"]] == sorted({n["module"] for n in body["nodes"]})


async def test_graph_before_survey(enrolled, web_app) -> None:
    body = (await enrolled.get(GRAPH)).json()

    assert body["survey_done"] is False
    assert body["current"] is None
    assert body["pending_item"] is False
    assert repos.get_open_session(_db(web_app)) is None  # чтение сессию не заводит


async def test_graph_shows_current_closed_and_weak(enrolled, web_app) -> None:
    await _finish_survey(enrolled)
    conn = _db(web_app)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    order = CourseGraph.load(conn).topo_order()
    closed, weak = order[0], order[1]
    steps = [
        RouteStep(concept_id=closed, mode="full", status="closed", closed_at=1.0),
        RouteStep(concept_id=weak, mode="full", status="closed", closed_at=1.0, weak=True),
    ]
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"route": Route(steps=steps), "current_node_id": order[2]})
    )

    body = (await enrolled.get(GRAPH)).json()
    by_id = {node["id"]: node for node in body["nodes"]}

    assert body["survey_done"] is True
    assert body["current"] == order[2]
    assert by_id[order[2]]["status"] == "current"
    assert by_id[closed]["status"] == "closed" and by_id[closed]["weak"] is False
    assert by_id[weak]["status"] == "closed" and by_id[weak]["weak"] is True


async def test_graph_tells_about_pending_item(enrolled, web_app) -> None:
    """Флаг — из состояния сессии: с ним карта спрашивает перед переходом (аудит 74–75)."""
    await _finish_survey(enrolled)
    conn = _db(web_app)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    item_id = repos.get_items(conn)[0].id

    repos.update_session_state(conn, session_id, state.model_copy(update={"pending_item_id": item_id}))
    assert (await enrolled.get(GRAPH)).json()["pending_item"] is True
    repos.update_session_state(conn, session_id, state.model_copy(update={"pending_item_id": None}))
    assert (await enrolled.get(GRAPH)).json()["pending_item"] is False


async def test_graph_hides_unpublished_module(enrolled, web_app) -> None:
    conn = _db(web_app)
    last = max(topic.number for topic in repos.get_topics(conn))
    conn.execute("UPDATE topics SET published = 0 WHERE id = ?", (last,))
    conn.commit()

    body = (await enrolled.get(GRAPH)).json()
    ids = {node["id"] for node in body["nodes"]}

    assert all(node["module"] != last for node in body["nodes"])
    assert last not in [module["number"] for module in body["modules"]]
    assert all(edge["from"] in ids and edge["to"] in ids for edge in body["edges"])


async def test_graph_marks_modules_behind_pending_survey(enrolled, web_app) -> None:
    """Висит анкета модуля 2: переход в модули без анкеты сервер отклонит (409)."""
    await _finish_survey(enrolled)
    body = (await enrolled.get(GRAPH)).json()
    assert body["pending_survey"] is None
    target = next(node["id"] for node in body["nodes"] if node["module"] == 2)
    assert (await enrolled.post(f"/api/courses/{COURSE}/lesson/switch", json={"node_id": target})).status_code == 200

    body = (await enrolled.get(GRAPH)).json()

    assert body["pending_survey"] == 2
    by_module = {node["module"]: node["needs_survey"] for node in body["nodes"]}
    assert by_module[1] is False
    assert all(by_module[m] for m in by_module if m >= 2)
    blocked = next(node["id"] for node in body["nodes"] if node["module"] == 3)
    assert (await enrolled.post(f"/api/courses/{COURSE}/lesson/switch", json={"node_id": blocked})).status_code == 409


async def test_profile_has_no_graph_without_course_or_with_broken_course(student, web_app) -> None:
    assert 'id="knowledge"' not in (await student.get("/profile")).text
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert 'id="knowledge"' in (await student.get("/profile")).text

    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    pool = web_app.state.userdbs
    path = pool.path(user.id, COURSE)
    pool.close_all()
    path.write_bytes(b"not a database at all" * 100)
    html = (await student.get("/profile")).text

    assert 'id="knowledge"' not in html
    assert "knowledge-graph.js" not in html
