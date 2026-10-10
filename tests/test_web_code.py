"""Итог тестов кода из браузера: API и ядро (Срез 46)."""

import pytest

from llm_tutor.db import repos
from llm_tutor.student import survey
from llm_tutor.web import accounts
from web_fakes import ScriptedTutor

COURSE = "mlcourse-topic01"
BASE = f"/api/courses/{COURSE}/lesson"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю тему.")


def _answers() -> list[int]:
    progress, given = survey.Progress(), []
    while (key := progress.next_key()) is not None:
        index = min(1, len(survey.options_for(key)) - 1)
        progress, given = progress.answer(index), [*given, index]
    return given


@pytest.fixture
async def lesson(student):
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    response = await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})
    assert response.status_code == 200
    return student


def _db(web_app):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, COURSE)


async def _runnable_item(client, node_id: str = "boolean_indexing") -> dict:
    await client.post(f"{BASE}/switch", json={"node_id": node_id})
    for _ in range(8):
        state = (await client.post(f"{BASE}/task", json={})).json()["state"]
        if state["item"] and state["item"]["runnable"]:
            return state["item"]
        await client.post(f"{BASE}/skip", json={})
    pytest.fail("не нашлось задания с кодом")


async def test_runnable_item_carries_code_material(lesson) -> None:
    item = await _runnable_item(lesson)

    assert item["type"] == "code"
    assert "result" in item["starter"]
    assert "pd.DataFrame" in item["setup"]
    assert "assert" in item["tests"]


async def test_passed_code_is_autotest_evidence(lesson, web_app) -> None:
    item = await _runnable_item(lesson)

    body = (
        await lesson.post(
            f"{BASE}/code-result",
            json={"item_id": item["id"], "code": "result = df", "passed": True, "report": ""},
        )
    ).json()

    assert body["verdict"]["correct"] is True
    events = [e for e in repos.get_events(_db(web_app)) if e.item_id == item["id"]]
    assert events and all(e.source == "autotest" for e in events)
    assert "```python" not in body["messages"][0]["html"]  # код отрисован блоком
    assert "<pre><code" in body["messages"][0]["html"]
    assert "все проверки пройдены" in body["messages"][0]["html"]


async def test_failed_code_reports_why(lesson, web_app) -> None:
    item = await _runnable_item(lesson)

    body = (
        await lesson.post(
            f"{BASE}/code-result",
            json={"item_id": item["id"], "code": "result = None", "passed": False, "report": "Ожидается таблица"},
        )
    ).json()

    assert body["verdict"]["correct"] is False
    assert "Ожидается таблица" in body["messages"][0]["html"]
    events = [e for e in repos.get_events(_db(web_app)) if e.item_id == item["id"]]
    assert [e.result for e in events] == [0.0] * len(events)


async def test_code_result_for_stale_item(lesson) -> None:
    item = await _runnable_item(lesson)
    await lesson.post(f"{BASE}/skip", json={})

    response = await lesson.post(
        f"{BASE}/code-result", json={"item_id": item["id"], "code": "x", "passed": True}
    )

    assert response.status_code == 409


async def test_code_result_for_non_code_item(lesson) -> None:
    await lesson.post(f"{BASE}/switch", json={"node_id": "python_basics"})
    state = (await lesson.post(f"{BASE}/task", json={})).json()["state"]
    assert state["item"]["type"] != "code"

    response = await lesson.post(
        f"{BASE}/code-result", json={"item_id": state["item"]["id"], "code": "x", "passed": True}
    )

    assert response.status_code == 409


@pytest.mark.parametrize("passed", ["true", 1])
async def test_passed_must_be_boolean(lesson, passed) -> None:
    item = await _runnable_item(lesson)

    response = await lesson.post(
        f"{BASE}/code-result", json={"item_id": item["id"], "code": "x", "passed": passed}
    )

    assert response.status_code == 422
