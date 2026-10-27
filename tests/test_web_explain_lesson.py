"""«Объясни новичку» внутри занятия: API, блок состояния, блокировка курса (срез 94)."""

import pytest
from course_fixtures import LESSON_SEED_PATHS, finish_all_but, use_lessons_course
from test_web_lesson import _user_db
from web_fakes import ExplainLLM

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course_data
from llm_tutor.db import repos
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict
from llm_tutor.student import survey

COURSE = "mlcourse"
BASE = f"/api/courses/{COURSE}/lesson"
EXPLAIN = f"{BASE}/explain"
QUOTE = "loc берёт метки, а iloc позиции"


@pytest.fixture
def web_llm():
    return ExplainLLM()


@pytest.fixture
async def lessons_student(monkeypatch, student):
    use_lessons_course(monkeypatch)
    response = await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert response.status_code == 200
    return student


async def _finish_survey(client) -> None:
    config = load_course_data(LESSON_SEED_PATHS).topics[0].survey
    progress, answers = survey.Progress(config), []
    while progress.next_key() is not None:
        progress, answers = progress.answer(0), [*answers, 0]
    response = await client.post(f"{BASE}/survey/finish", json={"answers": answers})
    assert response.status_code == 200, response.text


def _prepare_close(conn, node: str) -> int:
    """Занятие ``node`` последнее незакрытое: остался верный ответ на итоговое задание."""
    finish_all_but(conn, node, topic_id=1)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    order = CourseGraph.load(conn).topo_order()
    steps = [
        step.model_copy(update={"status": "ahead", "closed_at": None})
        if order.index(step.concept_id) > order.index(node)
        else step
        for step in state.route.steps
    ]
    final = next(i for i in repos.get_items(conn) if i.final and node in i.concept_weights)
    sections = [s.id for s in CourseGraph.load(conn).concept(node).sections]
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(
            update={
                "route": state.route.model_copy(update={"steps": steps}),
                "pending_item_id": final.id,
                "passed_sections": sections,
                "section_id": None,
                "phase": "practice",
            }
        ),
    )
    return final.id


async def _close(client, web_app, node: str = "l1_select") -> dict:
    """Закрыть занятие верным ответом через API; вернуть ответ."""
    conn = _user_db(web_app)
    item_id = _prepare_close(conn, node)
    answer = repos.get_item(conn, item_id).answer
    response = await client.post(f"{BASE}/answer", json={"item_id": item_id, "answer": answer})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def held(lessons_student, web_app):
    """Занятие «Выборка данных» закрыто: висит обязательное объяснение."""
    await _finish_survey(lessons_student)
    await _close(lessons_student, web_app)
    return lessons_student


async def _state(client) -> dict:
    return (await client.get(f"{BASE}/state")).json()["state"]


def _counts(web_app) -> tuple[int, int]:
    conn = _user_db(web_app)
    return (
        conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
    )


def _cover(web_llm) -> None:
    web_llm.observations = [
        ExplainObservation(
            ideas=[IdeaVerdict(id="loc_vs_iloc", verdict="correct", quote=QUOTE)]
        )
    ]
    web_llm.replies = ["Кажется, понял!"]


# --- Состояние ---


async def test_state_shows_required_task(held) -> None:
    state = await _state(held)

    explain = state["explain"]
    assert explain["active"]["node_id"] == "l1_select"
    assert explain["active"]["kind"] == "required"
    assert explain["active"]["status"] == "active"
    assert explain["pending"] == "l1_select" and explain["blocked"] is True
    assert state["node"] is None and state["item"] is None
    assert state["messages"] == []  # справа — пустая ветка наставника


async def test_reload_keeps_threads(held, web_llm) -> None:
    await held.post(f"{EXPLAIN}/turn", json={"text": "Это про выборку строк"})
    chat = await held.post(f"{BASE}/chat", json={"text": "Не понимаю loc"})
    assert chat.status_code == 200

    first = await _state(held)
    second = await _state(held)

    assert first["explain"]["active"]["messages"] == second["explain"]["active"]["messages"]
    assert first["messages"] == second["messages"]
    novice = [m["role"] for m in first["explain"]["active"]["messages"]]
    assert novice == ["novice", "student", "novice"]
    assert [m["role"] for m in first["messages"]] == ["user", "assistant"]
    assert "Смотри" in first["messages"][1]["html"]


async def test_task_before_next_module_survey(lessons_student, web_app) -> None:
    """Последнее важное занятие модуля: задание раньше анкеты следующего модуля."""
    prompts = web_app.state.explain_prompts
    limits = prompts.limits.model_copy(update={"max_student_turns": 1})
    web_app.state.explain_prompts = prompts.model_copy(update={"limits": limits})
    await _finish_survey(lessons_student)
    await _close(lessons_student, web_app, "l1_group")

    state = await _state(lessons_student)
    assert state["survey"] is False and state["explain"]["active"]["node_id"] == "l1_group"
    assert state["node"] is None

    await lessons_student.post(f"{EXPLAIN}/turn", json={"text": "Это про группы"})
    after = (await lessons_student.post(f"{EXPLAIN}/continue", json={})).json()["state"]

    assert after["survey"] is True and after["explain"] is None


# --- Блокировка ---

BLOCKED = [
    ("next", {"node_id": "l1_group", "part": 1}),
    ("task", {}),
    ("answer", {"item_id": 3010, "answer": "x"}),
    ("code-result", {"item_id": 3010, "code": "x = 1", "passed": True}),
    ("skip", {}),
    ("stuck", {}),
    ("resume", {}),
    ("verify", {}),
    ("switch", {"node_id": "l1_group"}),
    ("review/start", {"kind": "due"}),
]


@pytest.mark.parametrize(("path", "body"), BLOCKED, ids=[path for path, _ in BLOCKED])
async def test_blocked_moves_409(held, web_app, path, body) -> None:
    before = _counts(web_app)

    response = await held.post(f"{BASE}/{path}", json=body)

    assert response.status_code == 409
    assert "Сначала объясни новичку" in response.json()["detail"]["message"]
    assert _counts(web_app) == before


# --- Ходы ---


async def test_novice_turn(held, web_llm) -> None:
    response = await held.post(f"{EXPLAIN}/turn", json={"text": "Это про выборку строк"})

    assert response.status_code == 200
    body = response.json()
    assert [m["role"] for m in body["messages"]] == ["student", "novice"]
    assert body["progress"] == {"done": 0, "total": 1}
    assert body["state"]["explain"]["active"]["turns_left"] == 9


async def test_chat_goes_to_mentor(held, web_app, web_llm) -> None:
    before = _counts(web_app)[1]
    tutor_calls = len(web_llm.calls["tutor"])

    response = await held.post(f"{BASE}/chat", json={"text": "Не понимаю loc"})

    assert response.status_code == 200
    assert [m["role"] for m in response.json()["messages"]] == ["user", "assistant"]
    assert len(web_llm.calls["mentor"]) == 1
    assert len(web_llm.calls["tutor"]) == tutor_calls
    # Реплики наставника лежат в журнале «Объясни», а не в чате урока.
    assert _counts(web_app)[1] == before


async def test_chat_without_task_is_tutor(lessons_student, web_app, web_llm) -> None:
    await _finish_survey(lessons_student)
    web_llm.tutor = ["Ответ тьютора урока"]

    response = await lessons_student.post(f"{BASE}/chat", json={"text": "Что такое группа?"})

    assert response.status_code == 200
    assert web_llm.calls["mentor"] == []
    assert "Ответ тьютора урока" in response.json()["messages"][-1]["html"]


async def test_whitespace_422(held) -> None:
    for text in ("", "   \n "):
        assert (await held.post(f"{EXPLAIN}/turn", json={"text": text})).status_code == 422


async def test_llm_failure_502(held, web_llm, web_app) -> None:
    web_llm.fail = {"novice"}

    response = await held.post(f"{EXPLAIN}/turn", json={"text": "Это про выборку"})

    assert response.status_code == 502
    active = (await _state(held))["explain"]["active"]
    assert [m["role"] for m in active["messages"]] == ["novice"]  # только заготовка


async def test_continue_flow(held, web_llm) -> None:
    early = await held.post(f"{EXPLAIN}/continue", json={})
    assert early.status_code == 409

    _cover(web_llm)
    await held.post(f"{EXPLAIN}/turn", json={"text": QUOTE})
    middle = await _state(held)
    assert middle["explain"]["ready"] is True and middle["explain"]["blocked"] is True
    assert middle["explain"]["active"]["status"] == "finished"
    assert middle["explain"]["active"]["report"] is not None

    done = await held.post(f"{EXPLAIN}/continue", json={})

    assert done.status_code == 200
    state = done.json()["state"]
    assert state["explain"]["active"] is None and state["explain"]["blocked"] is False
    assert state["node"]["id"] == "l1_group"
    assert (await held.post(f"{EXPLAIN}/continue", json={})).status_code == 409


async def test_voluntary_start_stop(held, web_llm, web_app) -> None:
    _cover(web_llm)
    await held.post(f"{EXPLAIN}/turn", json={"text": QUOTE})
    await held.post(f"{EXPLAIN}/continue", json={})

    started = await held.post(f"{EXPLAIN}/start", json={"node_id": "l1_select"})
    assert started.status_code == 200
    active = started.json()["state"]["explain"]["active"]
    assert (active["kind"], active["node_id"]) == ("voluntary", "l1_select")
    # Ходы курса не блокируются: добровольное объяснение — не задание.
    assert started.json()["state"]["explain"]["blocked"] is False
    # Но чат справа — наставник со своей (пока пустой) веткой, а не история урока.
    assert started.json()["state"]["messages"] == []
    assert (await held.post(f"{BASE}/task", json={})).status_code == 200

    await held.post(f"{EXPLAIN}/turn", json={"text": "что-то про loc"})
    stopped = await held.post(f"{EXPLAIN}/stop", json={})
    assert stopped.status_code == 200
    assert stopped.json()["report"]["end_reason"] == "button"

    not_closed = await held.post(f"{EXPLAIN}/start", json={"node_id": "l1_group"})
    assert not_closed.status_code == 400


async def test_stop_required_409(held) -> None:
    assert (await held.post(f"{EXPLAIN}/stop", json={})).status_code == 409


async def test_session_detail_and_settings(held, web_llm) -> None:
    session_id = (await _state(held))["explain"]["active"]["id"]

    detail = await held.get(f"{EXPLAIN}/sessions/{session_id}")
    assert detail.status_code == 200 and detail.json()["session"]["id"] == session_id
    assert (await held.get(f"{EXPLAIN}/sessions/999")).status_code == 404

    assert (await held.post(f"{EXPLAIN}/settings", json={"share": True})).json() == {"share": True}
    assert (await _state(held))["explain"]["share"] is True


async def test_old_explain_pages_redirect(lessons_student) -> None:
    for path in (f"/lesson/{COURSE}/explain", f"/lesson/{COURSE}/explain/7"):
        response = await lessons_student.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/lesson/{COURSE}"
