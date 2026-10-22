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


# --- плашка фрагмента в репликах (срез 77) ---


async def _ask(client, source: str, text: str) -> dict:
    response = await client.post(
        f"{BASE}/chat", json={"text": "почему?", "quote": {"source": source, "text": text}}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_turn_response_carries_quote(enrolled) -> None:
    await _finish(enrolled)

    body = await _ask(enrolled, "code", "df.drop('a', axis=1)")

    user, *rest = body["messages"]
    quote = user["quote"]
    assert quote["source"] == "code"
    assert quote["label"] == "Из моего кода"
    assert "<pre>" in quote["html"] and "<code" in quote["html"]
    assert "почему?" in user["html"]
    assert all(message["quote"] is None for message in rest)


@pytest.mark.parametrize(
    ("source", "label"), [("theory", "Из теории"), ("task", "Из задания"), ("code", "Из моего кода")]
)
async def test_quote_survives_reload(enrolled, source, label) -> None:
    await _finish(enrolled)
    sent = (await _ask(enrolled, source, "groupby делит строки"))["messages"][0]["quote"]

    messages = (await enrolled.get(f"{BASE}/state")).json()["state"]["messages"]

    user = [m for m in messages if m["role"] == "user"][-1]
    assert user["quote"] == sent
    assert user["quote"]["label"] == label


@pytest.mark.parametrize("source", ["theory", "code"])
@pytest.mark.parametrize("text", ["<script>alert(1)</script>", '<img src=x onerror="alert(1)">'])
async def test_quote_html_is_escaped(enrolled, source, text) -> None:
    await _finish(enrolled)

    html = (await _ask(enrolled, source, text))["messages"][0]["quote"]["html"]

    assert "<script" not in html and "<img" not in html
    assert "&lt;" in html


async def test_old_messages_have_no_quote(enrolled, web_app) -> None:
    await _finish(enrolled)
    conn = _user_db(web_app)
    session_id = repos.get_open_session(conn)
    repos.add_message(conn, session_id, "user", "старая реплика", ts=10.0**10)
    repos.add_message(conn, session_id, "user", "битый meta", ts=10.0**10 + 1, meta="{")

    messages = (await enrolled.get(f"{BASE}/state")).json()["state"]["messages"]

    old = {m["html"]: m for m in messages if m["role"] == "user"}
    assert old["<p>старая реплика</p>\n"]["quote"] is None
    assert old["<p>битый meta</p>\n"]["quote"] is None


# --- аудит среза 76 ---


async def test_huge_quote_names_its_field(enrolled) -> None:
    """Слишком длинный фрагмент — ошибка у quote.text, а не у поля вопроса."""
    await _finish(enrolled)

    response = await enrolled.post(
        f"{BASE}/chat",
        json={"text": "почему?", "quote": {"source": "code", "text": "x" * (6 * FOCUS_MAX_CHARS)}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "quote.text"


async def test_code_quote_keeps_indent(enrolled, web_app) -> None:
    await _finish(enrolled)

    await enrolled.post(
        f"{BASE}/chat",
        json={"text": "почему?", "quote": {"source": "code", "text": "    if a:\n        b()\n"}},
    )

    assert focus_from_meta(_user_messages(web_app)[-1].meta).text == "if a:\n    b()"


# --- панель выделения (срез 78) ---


def test_selection_js_questions_match_core() -> None:
    """Вопросы готовых кнопок в браузере — те же, что проверены на намерения."""
    from pathlib import Path

    from llm_tutor.core.context import FOCUS_QUESTIONS
    from llm_tutor.web.app import STATIC_DIR

    script = (Path(STATIC_DIR) / "js" / "selection.js").read_text(encoding="utf-8")
    for question in FOCUS_QUESTIONS:
        assert f'"{question}"' in script


# --- аудит среза 77 ---


@pytest.mark.parametrize("source", ["theory", "task"])
async def test_text_quote_is_shown_verbatim(enrolled, source) -> None:
    """Фрагмент — уже отрисованный текст: Markdown в нём не разбирается заново."""
    await _finish(enrolled)
    text = "# считаем длину\nn = df.__len__()\n* звёздочка <b>"

    html = (await _ask(enrolled, source, text))["messages"][0]["quote"]["html"]

    assert "<h1" not in html and "<strong" not in html and "<li" not in html
    assert "# считаем длину<br>" in html
    assert "df.__len__()" in html and "* звёздочка &lt;b&gt;" in html


def test_selection_js_has_no_extra_questions() -> None:
    """Аудит 78: сверка в обе стороны — лишний вопрос в JS тоже ошибка."""
    import re
    from pathlib import Path

    from llm_tutor.core.context import FOCUS_QUESTIONS
    from llm_tutor.web.app import STATIC_DIR

    script = (Path(STATIC_DIR) / "js" / "selection.js").read_text(encoding="utf-8")
    block = script[script.index("const QUESTIONS = [") : script.index("];", script.index("const QUESTIONS = ["))]
    found = re.findall(r'\["[^"]+", "([^"]+)"\]', block)
    assert sorted(found) == sorted(FOCUS_QUESTIONS)
