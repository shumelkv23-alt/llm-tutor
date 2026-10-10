"""API занятия: анкета, ходы, задания (Срез 41)."""

import asyncio

import pytest

from llm_tutor.core.turn import STALE_ITEM_REPLY
from llm_tutor.db import repos
from llm_tutor.student import survey
from llm_tutor.web import accounts
from web_fakes import ScriptedTutor

COURSE = "mlcourse-topic01"
BASE = f"/api/courses/{COURSE}/lesson"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю тему: groupby разбивает строки на группы.")


@pytest.fixture
async def enrolled(student):
    response = await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert response.status_code == 200
    return student


def _answers(choice: int) -> list[int]:
    """Полная анкета: на каждый вопрос — вариант ``choice`` (или последний)."""
    progress = survey.Progress()
    given: list[int] = []
    while (key := progress.next_key()) is not None:
        index = min(choice, len(survey.options_for(key)) - 1)
        progress = progress.answer(index)
        given.append(index)
    return given


def _user_db(web_app):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, COURSE)


async def _finish(client, choice: int = 1) -> dict:
    response = await client.post(f"{BASE}/survey/finish", json={"answers": _answers(choice)})
    assert response.status_code == 200, response.text
    return response.json()


# --- доступ ---


async def test_lesson_requires_enrollment(student) -> None:
    response = await student.get(f"{BASE}/state")

    assert response.status_code == 403


async def test_lesson_requires_login(web) -> None:
    assert (await web.get(f"{BASE}/state")).status_code == 401


async def test_unknown_course(enrolled) -> None:
    assert (await enrolled.get("/api/courses/nope/lesson/state")).status_code == 404


# --- анкета ---


async def test_state_before_survey(enrolled) -> None:
    state = (await enrolled.get(f"{BASE}/state")).json()["state"]

    assert state["survey"] is True


async def test_survey_next_walks_questions(enrolled) -> None:
    first = (await enrolled.post(f"{BASE}/survey/next", json={"answers": []})).json()

    assert first["question"]["key"] == survey.LEVEL_KEY
    assert first["question"]["options"] == list(survey.LEVEL_OPTIONS)
    assert first["question"]["can_back"] is False

    second = (await enrolled.post(f"{BASE}/survey/next", json={"answers": [1]})).json()
    assert second["question"]["key"] != survey.LEVEL_KEY
    assert second["question"]["number"] == 1
    assert second["question"]["can_back"] is True

    done = (await enrolled.post(f"{BASE}/survey/next", json={"answers": _answers(1)})).json()
    assert done["question"] is None
    assert len(done["summary"]["items"]) == len(survey.BLOCKS)


@pytest.mark.parametrize("answers", [[9], [1, 99], [-1], [True]])
async def test_survey_rejects_impossible_answers(enrolled, web_app, answers) -> None:
    response = await enrolled.post(f"{BASE}/survey/next", json={"answers": answers})
    finish = await enrolled.post(f"{BASE}/survey/finish", json={"answers": answers})

    assert response.status_code == 422
    assert finish.status_code == 422
    assert repos.get_facts(_user_db(web_app)) == {}


async def test_survey_finish_requires_complete_answers(enrolled, web_app) -> None:
    response = await enrolled.post(f"{BASE}/survey/finish", json={"answers": [1]})

    assert response.status_code == 422
    assert not survey.is_completed(_user_db(web_app))


async def test_survey_finish_starts_lesson(enrolled, web_app, web_llm) -> None:
    body = await _finish(enrolled)

    assert survey.is_completed(_user_db(web_app))
    texts = [message["html"] for message in body["messages"]]
    assert "Ближайшие шаги" in texts[0]
    assert any("groupby разбивает" in text for text in texts)
    state = body["state"]
    assert state["survey"] is False
    assert state["node"] is not None
    assert body["summary"]["items"]


async def test_survey_finish_twice_is_rejected(enrolled) -> None:
    await _finish(enrolled)

    again = await enrolled.post(f"{BASE}/survey/finish", json={"answers": _answers(1)})

    assert again.status_code == 409


async def test_parallel_finish_starts_lesson_once(enrolled, web_llm) -> None:
    answers = _answers(1)
    first, second = await asyncio.gather(
        enrolled.post(f"{BASE}/survey/finish", json={"answers": answers}),
        enrolled.post(f"{BASE}/survey/finish", json={"answers": answers}),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert len(web_llm.calls) == 1


async def test_turns_require_survey(enrolled) -> None:
    for path in ("chat", "task", "skip", "stuck", "resume", "verify"):
        body = {"text": "привет"} if path == "chat" else {}
        assert (await enrolled.post(f"{BASE}/{path}", json=body)).status_code == 409, path


# --- ходы ---


async def test_chat_turn(enrolled) -> None:
    await _finish(enrolled)

    body = (await enrolled.post(f"{BASE}/chat", json={"text": "что такое groupby?"})).json()

    assert body["messages"][0] == {"role": "user", "html": "<p>что такое groupby?</p>\n", "ts": None}
    assert body["messages"][1]["role"] == "assistant"
    assert body["state"]["messages"][-1]["role"] == "assistant"


async def test_chat_rejects_blank(enrolled) -> None:
    await _finish(enrolled)

    assert (await enrolled.post(f"{BASE}/chat", json={"text": "   "})).status_code == 422


async def test_model_reply_is_rendered_safely(enrolled, web_llm) -> None:
    await _finish(enrolled)
    web_llm.replies = ['<script>alert(1)</script> и [x](javascript:alert(1))']

    body = (await enrolled.post(f"{BASE}/chat", json={"text": "привет"})).json()

    html = body["messages"][-1]["html"]
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert "href" not in html


async def test_task_goes_to_practice_not_chat(enrolled) -> None:
    await _finish(enrolled)
    await enrolled.post(f"{BASE}/skip", json={})

    body = (await enrolled.post(f"{BASE}/task", json={})).json()

    item = body["state"]["item"]
    assert item is not None
    assert item["prompt_html"]
    assert "Практика" in body["messages"][-1]["html"]


async def test_stale_answer_is_409_and_writes_nothing(enrolled, web_app) -> None:
    await _finish(enrolled)
    await enrolled.post(f"{BASE}/skip", json={})
    state = (await enrolled.post(f"{BASE}/task", json={})).json()["state"]
    events_before = len(repos.get_events(_user_db(web_app)))

    response = await enrolled.post(
        f"{BASE}/answer", json={"item_id": state["item"]["id"] + 1000, "answer": "0"}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["message"] == STALE_ITEM_REPLY
    assert len(repos.get_events(_user_db(web_app))) == events_before


async def test_answer_without_pending_item_is_409(enrolled) -> None:
    await _finish(enrolled)
    await enrolled.post(f"{BASE}/skip", json={})

    response = await enrolled.post(f"{BASE}/answer", json={"item_id": 1, "answer": 0})

    assert response.status_code == 409


async def _pending_choice(client, web_app):
    """Висящее задание с вариантами (пропускаем остальные типы)."""
    await client.post(f"{BASE}/skip", json={})
    for _ in range(12):
        state = (await client.post(f"{BASE}/task", json={})).json()["state"]
        item = state["item"]
        if item and item["type"] == "choice":
            return repos.get_item(_user_db(web_app), item["id"])
        await client.post(f"{BASE}/skip", json={})
    pytest.fail("не нашлось задания с вариантами")


async def test_choice_answer_by_index(enrolled, web_app) -> None:
    await _finish(enrolled)
    item = await _pending_choice(enrolled, web_app)

    body = (
        await enrolled.post(f"{BASE}/answer", json={"item_id": item.id, "answer": int(item.answer)})
    ).json()

    assert body["messages"][0]["html"].startswith("<p>")
    assert any(event.item_id == item.id for event in repos.get_events(_user_db(web_app)))


async def test_choice_index_out_of_range(enrolled, web_app) -> None:
    await _finish(enrolled)
    item = await _pending_choice(enrolled, web_app)

    response = await enrolled.post(f"{BASE}/answer", json={"item_id": item.id, "answer": 99})

    assert response.status_code == 422


async def test_parallel_chat_turns_are_serialized(enrolled, web_app, web_llm) -> None:
    await _finish(enrolled)
    before = len(repos.get_messages(_user_db(web_app), repos.get_open_session(_user_db(web_app))))

    await asyncio.gather(
        enrolled.post(f"{BASE}/chat", json={"text": "раз"}),
        enrolled.post(f"{BASE}/chat", json={"text": "два"}),
    )

    conn = _user_db(web_app)
    messages = repos.get_messages(conn, repos.get_open_session(conn))[before:]
    roles = [message.role for message in messages]
    assert roles == ["user", "assistant", "user", "assistant"]


async def test_switch_topic(enrolled) -> None:
    await _finish(enrolled)

    body = (await enrolled.post(f"{BASE}/switch", json={"node_id": "groupby"})).json()

    assert body["state"]["node"]["id"] == "groupby"
    assert "Перейти к теме" in body["messages"][0]["html"]
    assert (await enrolled.post(f"{BASE}/switch", json={"node_id": "nope"})).status_code == 404


async def test_verify_starts_check(enrolled) -> None:
    await _finish(enrolled)
    await enrolled.post(f"{BASE}/switch", json={"node_id": "pandas_intro"})

    state = (await enrolled.post(f"{BASE}/verify", json={})).json()["state"]

    assert state["mode"] == "verify"
    assert state["item"] is not None


async def test_core_failure_is_a_reply_not_500(enrolled, web_llm) -> None:
    await _finish(enrolled)

    async def broken(*args, **kwargs):
        raise RuntimeError("модель упала")

    web_llm.chat_structured = broken
    response = await enrolled.post(f"{BASE}/chat", json={"text": "привет"})

    assert response.status_code == 200
    assert response.json()["messages"][-1]["role"] == "assistant"


async def test_students_lessons_are_isolated(enrolled, web, web_app) -> None:
    await _finish(enrolled)
    await enrolled.post(f"{BASE}/chat", json={"text": "секрет Ани"})
    await web.post("/api/auth/logout", json={})
    await web.post("/api/auth/register", json={"email": "bob@example.com", "name": "Боб", "password": "пароль-боба"})
    await web.post(f"/api/courses/{COURSE}/enroll", json={})

    state = (await web.get(f"{BASE}/state")).json()["state"]

    assert state["survey"] is True
    assert "секрет Ани" not in str(state)


async def test_closing_a_node_end_to_end(enrolled, web_app) -> None:
    """Сквозной сценарий: анкета → урок → верные ответы → тема закрыта."""
    await _finish(enrolled, choice=0)
    closed_before = (await enrolled.get(f"{BASE}/state")).json()["state"]["closed"]

    state = None
    for _ in range(12):
        item = await _pending_choice(enrolled, web_app)
        state = (
            await enrolled.post(
                f"{BASE}/answer", json={"item_id": item.id, "answer": int(item.answer)}
            )
        ).json()["state"]
        if state["closed"] > closed_before:
            break

    assert state is not None and state["closed"] > closed_before
