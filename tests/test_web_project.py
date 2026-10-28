"""API проекта по курсу: список с замком, ходы под замком ученика (срез 99).

Курс — настоящий `data/` (33 занятия): «пройденным» он становится прямой записью
маршрута в БД ученика, как у повторения.
"""

import pytest
from web_fakes import ProjectLLM

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.schemas import ProjectMentorReply
from llm_tutor.schemas import ProjectBrief, ProjectFile, Route, RouteStep, SessionState
from llm_tutor.web import accounts, courses

BASE = "/api/courses/mlcourse/project"
CARD = {
    "title": "Отток клиентов",
    "problem": "Предсказать уход клиента за месяц",
    "data": "Telco Customer Churn, 7 тысяч клиентов",
    "success": "ROC AUC выше логистической регрессии",
}
NOTES = {
    "data": {"done": "Загрузил и посмотрел типы", "findings": "Классы несбалансированы"},
    "baseline": {"model": "Логистическая регрессия", "metric": "ROC AUC 0.84 на тесте"},
    "improve": {"tried": "Случайный лес и подбор глубины", "result": "Лес даёт ROC AUC 0.87"},
    "conclusions": {"answer": "Уход предсказуем по договору", "limits": "Данные одной компании"},
}


@pytest.fixture
def web_llm():
    return ProjectLLM(
        replies=(ProjectMentorReply(reply="Давай уточним.", proposal={"title": "Отток клиентов"}),)
    )


@pytest.fixture
async def ready(student):
    """Ученик записан на mlcourse."""
    response = await student.post("/api/courses/mlcourse/enroll", json={})
    assert response.status_code == 200, response.text
    return student


def _conn(web_app, course: str = "mlcourse"):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, course)


def _set_route(web_app, status: str, course: str = "mlcourse") -> None:
    """Все занятия курса получают один статус в маршруте ученика."""
    conn = _conn(web_app, course)
    graph = CourseGraph.load(conn)
    steps = [
        RouteStep(concept_id=node, mode="full", status=status, closed_at=1.0)
        for node in graph.topo_order()
    ]
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(route=Route(steps=steps)))


def _pass_course(web_app) -> None:
    _set_route(web_app, "closed")


async def _start(client, brief_id: str = "churn") -> dict:
    response = await client.post(f"{BASE}/start", json={"brief_id": brief_id})
    assert response.status_code == 200, response.text
    return response.json()


def _card(view: dict) -> dict[str, str]:
    return {field["key"]: field["value"] for field in view["card"]}


async def _to_build(client) -> dict:
    assert (await client.put(f"{BASE}/card", json={"fields": CARD})).status_code == 200
    response = await client.post(f"{BASE}/approve", json={})
    assert response.status_code == 200, response.text
    return response.json()["project"]


async def _projects(client) -> dict[str, dict]:
    response = await client.get("/api/projects")
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()["courses"]}


async def test_overview_states(ready, web_app) -> None:
    items = await _projects(ready)
    assert set(items) == {"mlcourse", "trees-demo"}  # у isit-ml проекта нет
    assert items["trees-demo"]["state"] == "not_enrolled"
    assert items["mlcourse"]["state"] == "locked"
    assert items["mlcourse"]["progress"]["total"] == 33 and items["mlcourse"]["progress"]["closed"] == 0

    _pass_course(web_app)
    items = await _projects(ready)
    assert items["mlcourse"]["state"] == "choose"
    assert [brief["id"] for brief in items["mlcourse"]["briefs"]] == ["churn", "bikes", "own"]
    assert items["mlcourse"]["project"] is None

    await _start(ready)
    items = await _projects(ready)
    assert items["mlcourse"]["state"] == "active" and items["mlcourse"]["project"]["status"] == "idea"

    await _to_build(ready)
    for stage, fields in NOTES.items():
        assert (await ready.put(f"{BASE}/notes", json={"stage": stage, "fields": fields})).status_code == 200
        assert (await ready.post(f"{BASE}/complete", json={"stage": stage})).status_code == 200
    items = await _projects(ready)
    assert items["mlcourse"]["state"] == "done"


async def test_overview_not_enrolled(student) -> None:
    items = await _projects(student)
    assert items["mlcourse"]["state"] == "not_enrolled" and items["mlcourse"]["project"] is None


async def test_overview_requires_login(web) -> None:
    assert (await web.get("/api/projects")).status_code == 401


async def test_project_view_before_start(ready, web_app) -> None:
    response = await ready.get(BASE)
    assert response.status_code == 200
    body = response.json()
    assert body["project"] is None and body["unlocked"] is False and len(body["briefs"]) == 3
    _pass_course(web_app)
    assert (await ready.get(BASE)).json()["unlocked"] is True


async def test_start_locked_and_unknown(ready, web_app) -> None:
    response = await ready.post(f"{BASE}/start", json={"brief_id": "nope"})
    assert response.status_code == 400
    response = await ready.post(f"{BASE}/start", json={"brief_id": "churn"})
    assert response.status_code == 403
    assert repos.current_project(_conn(web_app)) is None

    _pass_course(web_app)
    view = (await _start(ready))["project"]
    assert view["status"] == "idea" and view["brief"]["id"] == "churn"
    assert [message["role"] for message in view["messages"]] == ["mentor"]

    response = await ready.post(f"{BASE}/start", json={"brief_id": "churn"})
    assert response.status_code == 409


async def test_chat_take_approve_flow(ready, web_app) -> None:
    _pass_course(web_app)
    await _start(ready)

    response = await ready.post(f"{BASE}/chat", json={"text": "Хочу про отток"})
    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assert [message["role"] for message in messages] == ["student", "mentor"]
    assert messages[0]["proposal"] is None
    proposal = messages[1]["proposal"]
    assert proposal["kind"] == "card" and proposal["stage"] is None
    assert [(f["key"], f["value"]) for f in proposal["fields"]] == [("title", "Отток клиентов")]
    assert "Давай уточним" in messages[1]["html"]

    response = await ready.post(f"{BASE}/take", json={"turn_id": messages[1]["id"]})
    assert response.status_code == 200
    assert _card(response.json()["project"])["title"] == "Отток клиентов"

    response = await ready.post(f"{BASE}/approve", json={})
    assert response.status_code == 422
    assert response.json()["detail"]["missing"] == ["problem", "data", "success"]

    assert (await ready.put(f"{BASE}/card", json={"fields": CARD})).status_code == 200
    view = (await ready.post(f"{BASE}/approve", json={})).json()["project"]
    assert view["status"] == "build"
    assert [stage["status"] for stage in view["stages"]] == ["current", "ahead", "ahead", "ahead"]


async def test_card_errors(ready, web_app) -> None:
    _pass_course(web_app)
    await _start(ready)
    assert (await ready.put(f"{BASE}/card", json={"fields": {"nope": "x"}})).status_code == 422
    assert (await ready.put(f"{BASE}/card", json={"fields": {"title": "x" * 81}})).status_code == 422
    await _to_build(ready)
    assert (await ready.put(f"{BASE}/card", json={"fields": {"title": "Новое"}})).status_code == 409


async def test_take_unknown_turn_404(ready, web_app) -> None:
    _pass_course(web_app)
    view = (await _start(ready))["project"]
    opening = view["messages"][0]["id"]
    for turn_id in (opening, 9999):
        response = await ready.post(f"{BASE}/take", json={"turn_id": turn_id})
        assert response.status_code == 404


async def test_notes_and_complete(ready, web_app) -> None:
    _pass_course(web_app)
    await _start(ready)
    await _to_build(ready)

    response = await ready.put(f"{BASE}/notes", json={"stage": "baseline", "fields": NOTES["baseline"]})
    assert response.status_code == 409
    response = await ready.post(f"{BASE}/complete", json={"stage": "data"})
    assert response.status_code == 422 and response.json()["detail"]["missing"] == ["done", "findings"]
    response = await ready.put(f"{BASE}/notes", json={"stage": "data", "fields": {"nope": "x"}})
    assert response.status_code == 422

    for stage, fields in NOTES.items():
        response = await ready.put(f"{BASE}/notes", json={"stage": stage, "fields": fields})
        assert response.status_code == 200, response.text
        view = (await ready.post(f"{BASE}/complete", json={"stage": stage})).json()["project"]

    assert view["status"] == "done"
    assert [stage["status"] for stage in view["stages"]] == ["done"] * 4
    values = {field["key"]: field["value"] for field in view["stages"][1]["fields"]}
    assert values["metric"] == NOTES["baseline"]["metric"]


async def test_can_flags(ready, web_app) -> None:
    _pass_course(web_app)
    view = (await _start(ready))["project"]
    assert view["can_approve"] is False
    await ready.put(f"{BASE}/card", json={"fields": CARD})
    assert (await ready.get(BASE)).json()["project"]["can_approve"] is True
    await ready.post(f"{BASE}/approve", json={})
    view = (await ready.get(BASE)).json()["project"]
    assert view["can_approve"] is False and view["can_complete"] is False
    await ready.put(f"{BASE}/notes", json={"stage": "data", "fields": NOTES["data"]})
    assert (await ready.get(BASE)).json()["project"]["can_complete"] is True


@pytest.mark.parametrize("text", ["   ", "x" * 4001, ""])
async def test_blank_and_long_chat_422(ready, web_app, text) -> None:
    _pass_course(web_app)
    await _start(ready)
    response = await ready.post(f"{BASE}/chat", json={"text": text})
    assert response.status_code == 422
    assert len(repos.get_project_turns(_conn(web_app), repos.current_project(_conn(web_app)).id)) == 1


async def test_chat_without_project_409(ready) -> None:
    response = await ready.post(f"{BASE}/chat", json={"text": "Привет"})
    assert response.status_code == 409


async def test_llm_failure_502(ready, web_app, web_llm) -> None:
    _pass_course(web_app)
    await _start(ready)
    web_llm.fail = True
    response = await ready.post(f"{BASE}/chat", json={"text": "Привет"})
    assert response.status_code == 502
    conn = _conn(web_app)
    assert len(repos.get_project_turns(conn, repos.current_project(conn).id)) == 1
    web_llm.fail = False
    assert (await ready.post(f"{BASE}/chat", json={"text": "Привет"})).status_code == 200


async def test_archive_and_restart(ready, web_app) -> None:
    _pass_course(web_app)
    await _start(ready)
    response = await ready.post(f"{BASE}/archive", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["project"] is None and len(body["briefs"]) == 3
    assert (await ready.post(f"{BASE}/archive", json={})).status_code == 409
    assert (await _start(ready, "bikes"))["project"]["brief"]["id"] == "bikes"


async def test_project_survives_course_reopened(ready, web_app) -> None:
    """Review Focus: провалы переоткрыли занятия — начатый проект продолжается."""
    _pass_course(web_app)
    await _start(ready)
    _set_route(web_app, "ahead")

    body = (await ready.get(BASE)).json()
    assert body["project"] is not None and body["unlocked"] is False
    assert (await ready.post(f"{BASE}/chat", json={"text": "Продолжим"})).status_code == 200
    items = await _projects(ready)
    assert items["mlcourse"]["state"] == "active"


async def test_project_keeps_brief_copy(ready, web_app, monkeypatch) -> None:
    """Review Focus: описание убрали из файла — проект живёт по своей копии."""
    _pass_course(web_app)
    original = (await _start(ready))["project"]["brief"]["title"]
    replaced = ProjectFile(
        template="ml",
        briefs=[ProjectBrief(id="other", title="Другое описание", summary="s", result="r")],
    )
    monkeypatch.setattr(courses, "project_file", lambda course: replaced)

    view = (await ready.get(BASE)).json()["project"]
    assert view["brief"]["title"] == original
    assert (await ready.post(f"{BASE}/chat", json={"text": "Привет"})).status_code == 200
    response = await ready.post(f"{BASE}/start", json={"brief_id": "churn"})
    assert response.status_code == 400


async def test_stale_second_tab_409(ready, web_app) -> None:
    """Review Focus: повтор по уже сменившемуся статусу или этапу — 409, данные целы."""
    _pass_course(web_app)
    await _start(ready)
    await ready.put(f"{BASE}/card", json={"fields": CARD})
    assert (await ready.post(f"{BASE}/approve", json={})).status_code == 200
    assert (await ready.post(f"{BASE}/approve", json={})).status_code == 409

    await ready.put(f"{BASE}/notes", json={"stage": "data", "fields": NOTES["data"]})
    assert (await ready.post(f"{BASE}/complete", json={"stage": "data"})).status_code == 200
    before = (await ready.get(BASE)).json()["project"]
    stale = await ready.post(f"{BASE}/complete", json={"stage": "data"})
    stale_notes = await ready.put(
        f"{BASE}/notes", json={"stage": "data", "fields": {"done": "Перезаписано"}}
    )
    assert stale.status_code == 409 and stale_notes.status_code == 409
    after = (await ready.get(BASE)).json()["project"]
    assert after["stages"] == before["stages"]


async def test_demo_course_project(ready, web_app) -> None:
    """У демо-курса свой проект: два описания, замок до прохождения пяти занятий."""
    demo = "/api/courses/trees-demo/project"
    assert (await ready.post("/api/courses/trees-demo/enroll", json={})).status_code == 200
    items = await _projects(ready)
    assert items["trees-demo"]["state"] == "locked"
    assert items["trees-demo"]["progress"]["total"] == 5
    view = (await ready.get(demo)).json()
    assert [brief["id"] for brief in view["briefs"]] == ["churn_forest", "own_trees"]
    assert view["unlocked"] is False

    _set_route(web_app, "closed", "trees-demo")
    response = await ready.post(f"{demo}/start", json={"brief_id": "churn_forest"})
    assert response.status_code == 200, response.text
    assert response.json()["project"]["status"] == "idea"


async def test_course_without_project_404(ready) -> None:
    assert (await ready.post("/api/courses/isit-ml/enroll", json={})).status_code == 200
    assert (await ready.get("/api/courses/isit-ml/project")).status_code == 404
    assert (await ready.post("/api/courses/isit-ml/project/chat", json={"text": "Привет"})).status_code == 404


async def test_not_enrolled_403(student) -> None:
    assert (await student.get(BASE)).status_code == 403
    assert (await student.post(f"{BASE}/start", json={"brief_id": "churn"})).status_code == 403


async def test_guest_401(web) -> None:
    assert (await web.get(BASE)).status_code == 401


async def test_mentor_text_is_markdown_rendered(ready, web_app, web_llm) -> None:
    """Реплика наставника — HTML с сервера без сырых тегов; ученика — тоже."""
    _pass_course(web_app)
    await _start(ready)
    web_llm.replies = [ProjectMentorReply(reply="<script>alert(1)</script> **важно**")]
    response = await ready.post(f"{BASE}/chat", json={"text": "<img src=x onerror=alert(1)>"})
    html = "".join(message["html"] for message in response.json()["messages"])
    assert "<script>" not in html and "<img" not in html and "<strong>важно</strong>" in html


async def test_chat_runs_under_student_lock(ready, web_app) -> None:
    """Ход идёт под замком ученика: пока замок занят, ход ждёт."""
    import asyncio

    _pass_course(web_app)
    await _start(ready)
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    lock = web_app.state.userdbs.lock(user.id, "mlcourse")
    async with lock:
        task = asyncio.create_task(ready.post(f"{BASE}/chat", json={"text": "Привет"}))
        await asyncio.sleep(0.1)
        assert not task.done()
    assert (await task).status_code == 200


async def test_done_project_kept_while_course_reopened(ready, web_app) -> None:
    """Аудит среза 101: готовый проект не уходит в архив, пока новый выбрать нельзя."""

    _pass_course(web_app)
    await _start(ready)
    await _to_build(ready)
    for stage, fields in NOTES.items():
        await ready.put(f"{BASE}/notes", json={"stage": stage, "fields": fields})
        await ready.post(f"{BASE}/complete", json={"stage": stage})
    assert (await ready.get(BASE)).json()["project"]["status"] == "done"

    _set_route(web_app, "ahead")  # провалы переоткрыли занятия
    response = await ready.post(f"{BASE}/archive", json={})
    assert response.status_code == 409
    body = (await ready.get(BASE)).json()
    assert body["project"]["status"] == "done" and body["unlocked"] is False

    _pass_course(web_app)
    assert (await ready.post(f"{BASE}/archive", json={})).status_code == 200


async def test_unfinished_project_can_be_dropped_while_course_reopened(ready, web_app) -> None:
    """Проект в работе можно бросить и на переоткрытом курсе: ученик сам выбрал это в диалоге."""
    _pass_course(web_app)
    await _start(ready)
    _set_route(web_app, "ahead")
    assert (await ready.post(f"{BASE}/archive", json={})).status_code == 200


# --- «Подтвердить и дальше», предложение объектом, быстрая кнопка (срез 103) ---

STAGE_REPLY = ProjectMentorReply(
    reply="Подведём итог этапа.",
    proposal={"done": "Загрузил CSV и посмотрел типы", "findings": "Ушло 26 процентов клиентов"},
)


async def _build_with_stage_proposal(ready, web_app, web_llm, reply=STAGE_REPLY):
    _pass_course(web_app)
    await _start(ready)
    await _to_build(ready)
    web_llm.replies = [reply]
    response = await ready.post(
        f"{BASE}/chat", json={"text": "загрузил csv, ушло 26 процентов клиентов"}
    )
    assert response.status_code == 200, response.text
    return response.json()["messages"][1]


async def test_accept_flow(ready, web_app, web_llm) -> None:
    mentor = await _build_with_stage_proposal(ready, web_app, web_llm)

    response = await ready.post(f"{BASE}/accept", json={"turn_id": mentor["id"]})

    assert response.status_code == 200, response.text
    view = response.json()["project"]
    assert view["stage"] == "baseline"
    assert [stage["status"] for stage in view["stages"]] == ["done", "current", "ahead", "ahead"]
    old = next(m for m in view["messages"] if m["id"] == mentor["id"])
    assert old["proposal"]["actionable"] is False


async def test_accept_errors(ready, web_app, web_llm) -> None:
    only_done = ProjectMentorReply(reply="Итог", proposal={"done": "Загрузил CSV и посмотрел типы"})
    mentor = await _build_with_stage_proposal(ready, web_app, web_llm, only_done)

    response = await ready.post(f"{BASE}/accept", json={"turn_id": 9999})
    assert response.status_code == 404
    response = await ready.post(f"{BASE}/accept", json={"turn_id": mentor["id"]})
    assert response.status_code == 422
    assert response.json()["detail"]["missing"] == ["findings"]
    assert (await ready.get(BASE)).json()["project"]["stage"] == "data"

    web_llm.replies = [STAGE_REPLY]
    full = (await ready.post(
        f"{BASE}/chat", json={"text": "загрузил csv, ушло 26 процентов клиентов"}
    )).json()["messages"][1]
    assert (await ready.post(f"{BASE}/accept", json={"turn_id": full["id"]})).status_code == 200
    stale = await ready.post(f"{BASE}/accept", json={"turn_id": full["id"]})
    assert stale.status_code == 409


async def test_accept_in_idea_conflict(ready, web_app) -> None:
    _pass_course(web_app)
    await _start(ready)
    mentor = (await ready.post(f"{BASE}/chat", json={"text": "Хочу про отток"})).json()["messages"][1]
    response = await ready.post(f"{BASE}/accept", json={"turn_id": mentor["id"]})
    assert response.status_code == 409


async def test_proposal_view_shape(ready, web_app) -> None:
    _pass_course(web_app)
    await _start(ready)
    mentor = (await ready.post(f"{BASE}/chat", json={"text": "Хочу про отток"})).json()["messages"][1]
    proposal = mentor["proposal"]
    assert proposal["fields"][0] == {
        "key": "title", "label": "Название проекта", "value": "Отток клиентов", "required": True,
    }
    assert proposal["actionable"] is True and proposal["title"] == "Предложение для карточки"

    await _to_build(ready)
    view = (await ready.get(BASE)).json()["project"]
    old = next(m for m in view["messages"] if m["id"] == mentor["id"])
    assert old["proposal"]["actionable"] is False


async def test_stage_proposal_view_shape(ready, web_app, web_llm) -> None:
    mentor = await _build_with_stage_proposal(ready, web_app, web_llm)
    proposal = mentor["proposal"]
    assert proposal["kind"] == "stage" and proposal["stage"] == "data"
    assert proposal["title"] == "Итог этапа «Данные и анализ»" and proposal["actionable"] is True
    assert [f["key"] for f in proposal["fields"]] == ["done", "findings"]
    assert all(f["required"] for f in proposal["fields"])


async def test_quick_reply(ready, web_app) -> None:
    _pass_course(web_app)
    view = (await _start(ready))["project"]
    assert view["quick_reply"] == "Предложи формулировки карточки"
    view = await _to_build(ready)
    assert view["quick_reply"] == "Подвести итог этапа"
    for stage, fields in NOTES.items():
        await ready.put(f"{BASE}/notes", json={"stage": stage, "fields": fields})
        response = await ready.post(f"{BASE}/complete", json={"stage": stage})
    assert response.json()["project"]["quick_reply"] is None


async def test_accept_runs_under_student_lock(ready, web_app, web_llm) -> None:
    import asyncio

    mentor = await _build_with_stage_proposal(ready, web_app, web_llm)
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    lock = web_app.state.userdbs.lock(user.id, "mlcourse")
    async with lock:
        task = asyncio.create_task(ready.post(f"{BASE}/accept", json={"turn_id": mentor["id"]}))
        await asyncio.sleep(0.1)
        assert not task.done()
    assert (await task).status_code == 200
