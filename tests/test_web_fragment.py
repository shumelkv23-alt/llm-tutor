"""Вопрос тьютору по выделенному фрагменту: API хода чата (срезы 76–78)."""

import pytest
from test_web_lesson import BASE, COURSE, _finish, _user_db
from web_fakes import ScriptedTutor

from llm_tutor.core.context import FOCUS_MAX_CHARS, Focus, focus_from_meta
from llm_tutor.db import repos


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю фрагмент.")


@pytest.fixture
async def enrolled(student):
    response = await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert response.status_code == 200
    return student


def _user_messages(web_app):
    conn = _user_db(web_app)
    return [m for m in repos.get_messages(conn, repos.get_open_session(conn)) if m.role == "user"]


async def test_chat_with_quote_reaches_tutor(enrolled, web_app, web_llm) -> None:
    await _finish(enrolled)

    response = await enrolled.post(
        f"{BASE}/chat",
        json={"text": "почему?", "quote": {"source": "theory", "text": "  groupby делит  "}},
    )

    assert response.status_code == 200, response.text
    prompt = web_llm.calls[-1][-1].content
    assert "из конспекта занятия" in prompt and "groupby делит" in prompt
    last = _user_messages(web_app)[-1]
    assert last.content == "почему?"
    assert focus_from_meta(last.meta) == Focus("theory", "groupby делит")


@pytest.mark.parametrize(
    ("quote", "field"),
    [
        ({"source": "theory", "text": ""}, "quote.text"),
        ({"source": "theory", "text": "   "}, "quote.text"),
        ({"source": "code", "text": "x" * (FOCUS_MAX_CHARS + 1)}, "quote.text"),
        ({"source": "chat", "text": "x"}, "quote.source"),
        ({"source": "code", "text": "x", "html": "<b>y</b>"}, None),
        ({"text": "x"}, None),
    ],
)
async def test_chat_quote_validation(enrolled, web_app, web_llm, quote, field) -> None:
    await _finish(enrolled)
    calls, before = len(web_llm.calls), len(_user_messages(web_app))

    response = await enrolled.post(f"{BASE}/chat", json={"text": "почему?", "quote": quote})

    assert response.status_code == 422
    if field is not None:
        assert response.json()["detail"]["field"] == field
    assert len(web_llm.calls) == calls
    assert len(_user_messages(web_app)) == before


async def test_chat_quote_exactly_max(enrolled, web_app) -> None:
    await _finish(enrolled)

    response = await enrolled.post(
        f"{BASE}/chat",
        json={"text": "почему?", "quote": {"source": "code", "text": "x" * FOCUS_MAX_CHARS}},
    )

    assert response.status_code == 200, response.text
    assert len(focus_from_meta(_user_messages(web_app)[-1].meta).text) == FOCUS_MAX_CHARS


async def test_chat_without_quote_unchanged(enrolled, web_app) -> None:
    await _finish(enrolled)

    response = await enrolled.post(f"{BASE}/chat", json={"text": "почему?"})

    assert response.status_code == 200
    assert _user_messages(web_app)[-1].meta is None


async def test_chat_quote_before_survey_is_409(enrolled) -> None:
    response = await enrolled.post(
        f"{BASE}/chat", json={"text": "почему?", "quote": {"source": "theory", "text": "x"}}
    )

    assert response.status_code == 409
