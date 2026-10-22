"""Ходы повторения в API и блок review в состоянии занятия (срез 82)."""

import time

import pytest
from course_fixtures import LESSON_SEED_PATHS, use_lessons_course
from test_lesson_interface import _unpublish
from test_turn import _set_state, _state
from test_web_lesson import _user_db
from web_fakes import ScriptedTutor

from llm_tutor.core import turn
from llm_tutor.course.seed import load_course_data
from llm_tutor.db import repos
from llm_tutor.schemas import Item, ReviewState
from llm_tutor.student import review, survey

BASE = "/api/courses/mlcourse/lesson"
GROUP_ITEMS = [3011, 3012, 3013, 3014]
PAST = 1.0  # срок в далёком прошлом: просрочено при любых часах


@pytest.fixture
def web_llm():
    return ScriptedTutor("A\n---\nB")


@pytest.fixture
async def lessons_student(monkeypatch, student):
    use_lessons_course(monkeypatch)
    response = await student.post("/api/courses/mlcourse/enroll", json={})
    assert response.status_code == 200
    return student


async def _finish_survey(client) -> dict:
    config = load_course_data(LESSON_SEED_PATHS).topics[0].survey
    progress, answers = survey.Progress(config), []
    while progress.next_key() is not None:
        progress, answers = progress.answer(0), [*answers, 0]
    response = await client.post(f"{BASE}/survey/finish", json={"answers": answers})
    assert response.status_code == 200, response.text
    return response.json()["state"]


def _make_due(web_app, *concept_ids: str, status: str = "closed") -> None:
    """Занятия закрыты в маршруте ученика и просрочены на лестнице."""
    conn = _user_db(web_app)
    route = _state(conn).route
    steps = [
        step.model_copy(update={"status": status, "closed_at": PAST})
        if step.concept_id in concept_ids
        else step
        for step in route.steps
    ]
    _set_state(conn, route=route.model_copy(update={"steps": steps}))
    for concept_id in concept_ids:
        repos.upsert_review(conn, concept_id, 1, PAST)


@pytest.fixture
async def ready(lessons_student, web_app):
    """Ученик прошёл анкету; занятие «Группировка» закрыто и просрочено."""
    await _finish_survey(lessons_student)
    _make_due(web_app, "l1_group")
    return lessons_student


def _events(web_app) -> int:
    return _user_db(web_app).execute("SELECT COUNT(*) FROM events").fetchone()[0]


def _correct_index(item_id: int, web_app) -> int:
    item = repos.get_item(_user_db(web_app), item_id)
    return int(item.answer)


async def _start(client, kind: str = "due", **body) -> dict:
    response = await client.post(f"{BASE}/review/start", json={"kind": kind, **body})
    assert response.status_code == 200, response.text
    return response.json()


async def test_start_due_and_answer(ready, web_app) -> None:
    body = await _start(ready)

    state = body["state"]
    assert state["item"]["id"] in GROUP_ITEMS
    assert state["review"]["active"] == {
        "kind": "due", "node_name": "Группировка", "position": 1, "total": 1
    }
    assert body["messages"][-1]["html"].count("Практика") >= 1  # отметка о задании в чате

    answered = await ready.post(
        f"{BASE}/answer",
        json={"item_id": state["item"]["id"], "answer": _correct_index(state["item"]["id"], web_app)},
    )

    assert answered.status_code == 200, answered.text
    result = answered.json()
    assert result["verdict"]["correct"] is True
    assert result["state"]["review"]["active"] is None
    assert "верно 1 из 1" in result["messages"][-1]["html"]
    assert repos.get_review_rows(_user_db(web_app))["l1_group"].step == 2


async def test_start_409_when_active(ready) -> None:
    await _start(ready)
    response = await ready.post(f"{BASE}/review/start", json={"kind": "due"})
    assert response.status_code == 409


async def test_start_409_before_survey(lessons_student) -> None:
    response = await lessons_student.post(f"{BASE}/review/start", json={"kind": "due"})
    assert response.status_code == 409
    for path in ("stop", "settings"):
        body = {"enabled": False} if path == "settings" else None
        r = await lessons_student.post(f"{BASE}/review/{path}", json=body or {})
        assert r.status_code == 409, path


async def test_refresh_400_on_unclosed_or_foreign(ready) -> None:
    for node_ids in (["l1_select"], ["nope"], ["l1_group", "l1_select"]):
        response = await ready.post(
            f"{BASE}/review/start", json={"kind": "refresh", "node_ids": node_ids}
        )
        assert response.status_code == 400, node_ids
    state = (await ready.get(f"{BASE}/state")).json()["state"]
    assert state["review"]["active"] is None


async def test_refresh_asks_chosen_closed_lesson(ready) -> None:
    body = await _start(ready, "refresh", node_ids=["l1_group"])
    assert body["state"]["review"]["active"]["kind"] == "refresh"


async def test_start_rejects_unknown_kind(ready) -> None:
    response = await ready.post(f"{BASE}/review/start", json={"kind": "warmup"})
    assert response.status_code == 422


async def test_nothing_to_review_answers_without_item(lessons_student) -> None:
    await _finish_survey(lessons_student)

    body = await _start(lessons_student)

    assert "Повторять пока нечего" in body["messages"][-1]["html"]
    assert body["state"]["item"] is None and body["state"]["review"]["active"] is None


async def test_draft_course_404(student, make_course) -> None:
    make_course("draft-course", status="draft")
    for path in ("start", "stop", "settings"):
        response = await student.post(
            f"/api/courses/draft-course/lesson/review/{path}", json={"kind": "due", "enabled": True}
        )
        assert response.status_code == 404, path


async def test_review_post_requires_origin(ready) -> None:
    for path, body in (("start", {"kind": "due"}), ("stop", {}), ("settings", {"enabled": False})):
        response = await ready.post(
            f"{BASE}/review/{path}", json=body, headers={"Origin": "http://evil.example"}
        )
        assert response.status_code == 403, path
    state = (await ready.get(f"{BASE}/state")).json()["state"]
    assert state["review"]["active"] is None and state["review"]["enabled"] is True


async def test_lesson_item_answer_stale_during_review(ready, web_app) -> None:
    """Две вкладки: в одной идёт серия, из другой приходит ответ на задание урока."""
    task = await ready.post(f"{BASE}/task", json={})
    lesson_item = task.json()["state"]["item"]["id"]
    assert lesson_item not in GROUP_ITEMS
    await _start(ready)
    events, pending = _events(web_app), _state(_user_db(web_app)).pending_item_id

    response = await ready.post(f"{BASE}/answer", json={"item_id": lesson_item, "answer": 0})

    assert response.status_code == 409
    assert _events(web_app) == events
    state = _state(_user_db(web_app))
    assert state.pending_item_id == pending == lesson_item
    assert state.review is not None and state.attempts == 0


async def test_code_result_for_lesson_item_stale_during_review(ready, web_app) -> None:
    conn = _user_db(web_app)
    code = Item(
        id=2999, prompt="Напиши код", answer_type="code", tests="assert result == 1",
        starter="result = 0", concept_weights={"l1_frames": 1.0}, section="series",
    )
    repos.upsert_item(conn, code)
    task = await ready.post(f"{BASE}/task", json={})
    assert task.json()["state"]["item"]["id"] == 2999
    await _start(ready)
    events = _events(web_app)

    response = await ready.post(
        f"{BASE}/code-result", json={"item_id": 2999, "code": "result = 1", "passed": True}
    )

    assert response.status_code == 409 and _events(web_app) == events


async def _with_code_review_item(web_app) -> None:
    """В занятии «Группировка» первым по очереди — задание с кодом."""
    repos.upsert_item(
        _user_db(web_app),
        Item(
            id=2998, prompt="Напиши код", answer_type="code", tests="assert result == 1",
            starter="result = 0", concept_weights={"l1_group": 1.0}, section="groupby",
        ),
    )


async def test_code_result_scores_review_item(ready, web_app) -> None:
    await _with_code_review_item(web_app)
    body = await _start(ready)
    assert body["state"]["item"]["id"] == 2998

    response = await ready.post(
        f"{BASE}/code-result", json={"item_id": 2998, "code": "result = 1", "passed": True}
    )

    assert response.status_code == 200, response.text
    assert response.json()["verdict"]["correct"] is True
    row = _user_db(web_app).execute(
        "SELECT concept_id, source FROM events WHERE item_id = 2998"
    ).fetchone()
    assert tuple(row) == ("l1_group", "autotest")


async def test_code_result_after_stop_is_stale(ready, web_app) -> None:
    await _with_code_review_item(web_app)
    await _start(ready)
    stopped = await ready.post(f"{BASE}/review/stop", json={})
    assert stopped.status_code == 200
    events = _events(web_app)

    response = await ready.post(
        f"{BASE}/code-result", json={"item_id": 2998, "code": "result = 1", "passed": True}
    )

    assert response.status_code == 409 and _events(web_app) == events


async def test_answer_after_stop_is_stale(ready, web_app) -> None:
    body = await _start(ready)
    item_id = body["state"]["item"]["id"]
    await ready.post(f"{BASE}/review/stop", json={})
    events = _events(web_app)

    response = await ready.post(f"{BASE}/answer", json={"item_id": item_id, "answer": 0})

    assert response.status_code == 409 and _events(web_app) == events


async def test_double_submit_second_answer_is_stale(ready, web_app) -> None:
    """Повторная отправка того же ответа не засчитывается следующему заданию серии."""
    _make_due(web_app, "l1_select")
    body = await _start(ready)
    first = body["state"]["item"]["id"]
    index = _correct_index(first, web_app)
    assert (await ready.post(f"{BASE}/answer", json={"item_id": first, "answer": index})).status_code == 200
    events = _events(web_app)

    again = await ready.post(f"{BASE}/answer", json={"item_id": first, "answer": index})

    assert again.status_code == 409 and _events(web_app) == events


async def test_switch_aborts_review(ready, web_app) -> None:
    await _start(ready)

    response = await ready.post(f"{BASE}/switch", json={"node_id": "l1_select"})

    assert response.status_code == 200, response.text
    assert response.json()["state"]["review"]["active"] is None
    state = _state(_user_db(web_app))
    assert state.review is None and state.current_node_id == "l1_select"
    assert repos.get_review_rows(_user_db(web_app))["l1_group"].due_at == PAST  # без отсрочки


async def test_task_and_verify_abort_review(ready, web_app) -> None:
    await _start(ready)
    assert (await ready.post(f"{BASE}/task", json={})).status_code == 200
    assert _state(_user_db(web_app)).review is None
    await _start(ready)
    assert (await ready.post(f"{BASE}/verify", json={})).status_code == 200
    assert _state(_user_db(web_app)).review is None


def _warmup_state(web_app) -> None:
    """Разминка перед занятием: висит задание «Группировки», урок ещё не начат."""
    conn = _user_db(web_app)
    item = review.pick_item(conn, "l1_group", now=time.time())
    _set_state(
        conn,
        review=ReviewState(
            kind="warmup", queue=[], node_id="l1_group", item_id=item.id, total=1
        ),
        lesson_parts=[], lesson_part=0, pending_item_id=None,
    )


async def test_next_in_warmup_stops_and_starts_lesson(ready, web_app) -> None:
    _warmup_state(web_app)

    response = await ready.post(f"{BASE}/next", json={"node_id": "l1_frames", "part": 0})

    assert response.status_code == 200, response.text
    state = _state(_user_db(web_app))
    assert state.review is None and state.lesson_part == 1
    row = repos.get_review_rows(_user_db(web_app))["l1_group"]
    assert row.due_at > time.time()
    assert response.json()["state"]["lesson_button"] is not None


async def test_next_during_due_series_aborts_and_proceeds(ready, web_app) -> None:
    await _start(ready)
    state = _state(_user_db(web_app))
    node = state.current_node_id

    response = await ready.post(f"{BASE}/next", json={"node_id": node, "part": state.lesson_part})

    assert response.status_code == 200, response.text
    assert _state(_user_db(web_app)).review is None


async def test_stop_series_and_409_without_series(ready, web_app) -> None:
    assert (await ready.post(f"{BASE}/review/stop", json={})).status_code == 409
    await _start(ready)

    response = await ready.post(f"{BASE}/review/stop", json={})

    assert response.status_code == 200, response.text
    assert response.json()["state"]["review"]["active"] is None
    assert repos.get_review_rows(_user_db(web_app))["l1_group"].due_at == PAST


async def test_stop_warmup_postpones(ready, web_app) -> None:
    _warmup_state(web_app)

    response = await ready.post(f"{BASE}/review/stop", json={})

    assert response.status_code == 200, response.text
    assert repos.get_review_rows(_user_db(web_app))["l1_group"].due_at > time.time()
    assert _state(_user_db(web_app)).lesson_part == 1


async def test_chat_stuck_and_skip_during_review(ready, web_app, web_llm) -> None:
    _make_due(web_app, "l1_select")
    body = await _start(ready)
    first_node = body["state"]["review"]["active"]["node_name"]

    chat = await ready.post(f"{BASE}/chat", json={"text": "подскажи, пожалуйста"})
    assert chat.status_code == 200, chat.text
    assert _state(_user_db(web_app)).review.hinted is True
    stuck = await ready.post(f"{BASE}/stuck", json={})
    assert stuck.status_code == 200 and turn.STUCK_NOTE not in stuck.text

    skipped = await ready.post(f"{BASE}/skip", json={})

    assert skipped.status_code == 200, skipped.text
    active = skipped.json()["state"]["review"]["active"]
    assert active["node_name"] != first_node and active["position"] == 2
    assert _state(_user_db(web_app)).review.hinted is False


async def test_resume_during_review_changes_nothing(ready, web_app) -> None:
    body = await _start(ready)
    before = _state(_user_db(web_app)).model_dump()

    response = await ready.post(f"{BASE}/resume", json={})

    assert response.status_code == 200, response.text
    assert _state(_user_db(web_app)).model_dump() == before
    assert response.json()["state"]["item"]["id"] == body["state"]["item"]["id"]


async def test_state_has_review_block(ready, web_app) -> None:
    state = (await ready.get(f"{BASE}/state")).json()["state"]

    assert state["review"]["enabled"] is True
    assert state["review"]["due"] == 1 and state["review"]["active"] is None
    assert state["review"]["lessons"] == [
        {"module": 1, "id": "l1_group", "name": "Группировка", "due": True}
    ]
    flags = {t["id"]: t["review_due"] for t in state["topics"]}
    assert flags["l1_group"] is True and flags["l1_frames"] is False


async def test_settings_toggle(ready, web_app) -> None:
    off = await ready.post(f"{BASE}/review/settings", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["state"]["review"]["enabled"] is False
    assert off.json()["state"]["review"]["due"] == 1  # расписание считается
    assert repos.get_fact(_user_db(web_app), "review_enabled") == "0"

    on = await ready.post(f"{BASE}/review/settings", json={"enabled": True})
    assert on.json()["state"]["review"]["enabled"] is True

    bad = await ready.post(f"{BASE}/review/settings", json={"enabled": "да"})
    assert bad.status_code == 422


async def test_manual_start_works_when_disabled(ready, web_app) -> None:
    await ready.post(f"{BASE}/review/settings", json={"enabled": False})
    body = await _start(ready)
    assert body["state"]["review"]["active"]["kind"] == "due"


async def _card(client) -> dict:
    cards = (await client.get("/api/courses")).json()["courses"]
    return next(c for c in cards if c["id"] == "mlcourse")


async def test_card_review_due_counts_and_hides_disabled(ready, web_app) -> None:
    card = await _card(ready)
    assert card["review_due"] == 1 and card["review_overdue"] > 0

    await ready.post(f"{BASE}/review/settings", json={"enabled": False})
    card = await _card(ready)
    assert card["review_due"] == 0


async def test_card_zero_when_not_enrolled(student) -> None:
    cards = (await student.get("/api/courses")).json()["courses"]
    assert all(c["review_due"] == 0 for c in cards)


async def test_card_skips_missing_lesson(ready, web_app) -> None:
    """Модуль снят с публикации: его занятие не попадает ни в серию, ни в число на главной."""
    _make_due(web_app, "l2_hist")
    assert (await _card(ready))["review_due"] == 2

    _unpublish(_user_db(web_app), 2)

    assert (await _card(ready))["review_due"] == 1
    state = (await ready.get(f"{BASE}/state")).json()["state"]
    assert state["review"]["due"] == 1
    assert [x["id"] for x in state["review"]["lessons"]] == ["l1_group"]
    body = await _start(ready)
    assert body["state"]["review"]["active"]["total"] == 1


# --- страница занятия и главная ---


async def test_lesson_page_has_review_tab(ready) -> None:
    html = (await ready.get("/lesson/mlcourse")).text

    assert 'id="tab-btn-review"' in html and 'aria-controls="tab-review"' in html
    assert 'id="tab-review"' in html and 'aria-labelledby="tab-btn-review"' in html
    assert "/static/js/review.js?v=" in html
    assert 'style="' not in html.split('id="tab-review"')[1].split("</section>")[0]


async def test_home_shows_review_today_block(ready) -> None:
    html = (await ready.get("/")).text

    assert "Повторить сегодня" in html
    assert 'href="/lesson/mlcourse#review"' in html
    assert "к повторению: <b>1</b>" in html


async def test_courses_page_card_shows_review_due(ready) -> None:
    html = (await ready.get("/courses")).text
    assert "к повторению: <b>1</b>" in html


async def test_home_without_due_has_no_block(lessons_student) -> None:
    await _finish_survey(lessons_student)
    html = (await lessons_student.get("/")).text
    assert "Повторить сегодня" not in html and "к повторению" not in html


async def test_home_hides_block_for_disabled_course(ready) -> None:
    await ready.post(f"{BASE}/review/settings", json={"enabled": False})
    html = (await ready.get("/")).text
    assert "Повторить сегодня" not in html and "к повторению" not in html


async def test_home_block_orders_courses_by_overdue(ready, make_course, web_app) -> None:
    """Самые просроченные курсы — сверху блока."""
    from llm_tutor.schemas import Route, RouteStep, SessionState
    from llm_tutor.web import accounts

    make_course("python-basics", status="published")
    await ready.post("/api/courses/python-basics/enroll", json={})
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    conn = web_app.state.userdbs.open(user.id, "python-basics")
    node = conn.execute("SELECT id FROM concepts ORDER BY id").fetchone()["id"]
    route = Route(steps=[RouteStep(concept_id=node, mode="full", status="closed", closed_at=1.0)])
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(route=route))
    repos.upsert_review(conn, node, 1, time.time() - 3_600)  # просрочено на час, у mlcourse — на десятилетия

    html = (await ready.get("/")).text

    block = html.split("Повторить сегодня")[1]
    assert block.index("/lesson/mlcourse#review") < block.index("/lesson/python-basics#review")


# --- аудит среза 82 ---


async def test_stale_next_button_does_not_abort_series(ready, web_app) -> None:
    """Старая кнопка «Дальше» из другой вкладки не гасит идущую серию."""
    await _start(ready)
    state = _state(_user_db(web_app))

    response = await ready.post(
        f"{BASE}/next", json={"node_id": state.current_node_id, "part": state.lesson_part + 7}
    )

    assert response.status_code == 200, response.text
    after = _state(_user_db(web_app))
    assert after.review is not None and after.review.item_id == state.review.item_id
    assert after.lesson_part == state.lesson_part


async def test_stale_next_button_does_not_stop_warmup(ready, web_app) -> None:
    _warmup_state(web_app)

    response = await ready.post(f"{BASE}/next", json={"node_id": "l1_select", "part": 3})

    assert response.status_code == 200, response.text
    assert _state(_user_db(web_app)).review is not None
    assert repos.get_review_rows(_user_db(web_app))["l1_group"].due_at == PAST


async def test_refresh_node_ids_are_bounded(ready) -> None:
    long_id = "x" * 201
    response = await ready.post(
        f"{BASE}/review/start", json={"kind": "refresh", "node_ids": [long_id]}
    )
    assert response.status_code == 422
    too_many = await ready.post(
        f"{BASE}/review/start", json={"kind": "refresh", "node_ids": ["l1_group"] * 101}
    )
    assert too_many.status_code == 422
