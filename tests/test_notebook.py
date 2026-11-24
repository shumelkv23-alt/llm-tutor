"""Личный конспект (ветка sanya): пометка вопросов и страница «Конспект» (2026-11-20).

В ветке sanya файл назывался test_notes.py: имя занято заметками ветки golos.
"""

import json

import pytest
from legacy_course import load_seed
from test_web_lesson import BASE, COURSE, _finish, _user_db
from web_fakes import ScriptedTutor

from llm_tutor.core.turn import post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.db import repos
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.schemas import Event, SessionState
from llm_tutor.web import accounts, notes

REPLY = "Группировка делит строки по ключу."
TAKEAWAY = "groupby делит строки по ключу и считает агрегаты по группам."


@pytest.fixture(autouse=True)
def _visible_builtins(visible_builtins):
    """Тест идёт по занятиям mlcourse, который после слияния ветки admin скрыт."""
    return visible_builtins


@pytest.fixture
def web_llm():
    return ScriptedTutor(REPLY, takeaway=TAKEAWAY)


@pytest.fixture
async def enrolled(student):
    response = await student.post(f"/api/courses/{COURSE}/enroll", json={})
    assert response.status_code == 200
    return student


def _add(
    conn,
    ts: float,
    question: str,
    *,
    node: str | None = None,
    section: str | None = None,
    quote: dict | None = None,
    answer: str = "Ответ тьютора.",
    takeaway: str | None = None,
):
    """Реплика вопроса и ответ тьютора — как их пишет post_turn (один ts)."""
    session_id = repos.ensure_open_session(conn, ts)
    meta: dict = {"question": True}
    if node:
        meta["node"] = node
    if section:
        meta["section"] = section
    if quote:
        meta["quote"] = quote
    answer_meta = {"takeaway": takeaway} if takeaway else None
    repos.add_message(
        conn, session_id, "user", question, ts=ts, meta=json.dumps(meta, ensure_ascii=False)
    )
    repos.add_message(
        conn,
        session_id,
        "assistant",
        answer,
        ts=ts,
        meta=json.dumps(answer_meta, ensure_ascii=False) if answer_meta else None,
    )


def _solution(
    conn,
    ts: float,
    text: str,
    item,
    *,
    result: float = 1.0,
    source: str = "checked",
):
    """Ответ на задание — как его пишет ход: реплика ученика и событие с одним ts."""
    session_id = repos.ensure_open_session(conn, ts)
    repos.add_message(conn, session_id, "user", text, ts=ts)
    repos.add_event(
        conn,
        Event(
            source=source,
            result=result,
            item_id=item.id,
            concept_id=main_concept(item),
            ts=ts,
        ),
    )


# --- пометка в журнале ---


def test_post_turn_marks_question_lesson_and_section(conn) -> None:
    session_id = repos.create_session(conn, now=1.0)

    post_turn(
        conn,
        session_id,
        user_text="Что такое groupby?",
        assistant_text="Разбивает строки на группы.",
        state=SessionState(current_node_id="groupby", section_id="group"),
        now=2.0,
        question=True,
    )

    users = [message for message in repos.get_messages(conn, session_id) if message.role == "user"]
    assert json.loads(users[0].meta) == {
        "question": True,
        "node": "groupby",
        "section": "group",
    }


def test_post_turn_answer_is_not_a_question(conn) -> None:
    session_id = repos.create_session(conn, now=1.0)

    post_turn(
        conn,
        session_id,
        user_text="42",
        assistant_text="Верно ✓",
        state=SessionState(current_node_id="groupby"),
        now=2.0,
    )

    users = [message for message in repos.get_messages(conn, session_id) if message.role == "user"]
    assert users[0].meta is None


def test_post_turn_stores_takeaway_in_assistant_meta(conn) -> None:
    session_id = repos.create_session(conn, now=1.0)

    post_turn(
        conn,
        session_id,
        user_text="Что такое groupby?",
        assistant_text="Разбивает строки на группы.",
        state=SessionState(current_node_id="groupby"),
        now=2.0,
        question=True,
        takeaway="groupby делит строки по ключу.",
    )

    assistant = [
        message for message in repos.get_messages(conn, session_id) if message.role == "assistant"
    ][0]
    assert json.loads(assistant.meta)["takeaway"] == "groupby делит строки по ключу."


def test_post_turn_skips_empty_takeaway(conn) -> None:
    session_id = repos.create_session(conn, now=1.0)

    post_turn(
        conn,
        session_id,
        user_text="Что такое groupby?",
        assistant_text="Разбивает строки на группы.",
        state=SessionState(current_node_id="groupby"),
        now=2.0,
        question=True,
        takeaway="   ",
    )

    assistant = [
        message for message in repos.get_messages(conn, session_id) if message.role == "assistant"
    ][0]
    assert assistant.meta is None


# --- сборка конспекта ---


def test_lesson_notes_group_by_lesson_newest_first(conn) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    _add(conn, 1.0, "Старый про groupby", node="groupby", answer="Старый ответ")
    _add(conn, 2.0, "Про sorting", node="sorting")
    _add(conn, 3.0, "Новый про groupby", node="groupby", answer="Новый ответ")

    groups = notes.lesson_notes(conn)

    assert [group["node_id"] for group in groups] == ["groupby", "sorting"]
    assert groups[0]["name"] == graph.concept("groupby").name
    assert [entry["question"] for entry in groups[0]["entries"]] == [
        "Новый про groupby",
        "Старый про groupby",
    ]
    assert "Новый ответ" in groups[0]["entries"][0]["answer_html"]


def test_lesson_notes_keep_quote_source_and_section(conn) -> None:
    load_seed(conn)
    _add(
        conn,
        1.0,
        "Почему так?",
        node="groupby",
        section="group",
        quote={"source": "code", "text": "  df.groupby('x')"},
    )

    entry = notes.lesson_notes(conn)[0]["entries"][0]

    # API нормализует фрагмент перед записью; здесь он сохранён как есть.
    assert entry["quote"] == "  df.groupby('x')"
    assert entry["source"] == "Код" and entry["is_code"] is True
    assert entry["section_id"] == "group"


def test_lesson_notes_skip_messages_without_question_marker(conn) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, 1.0)
    repos.add_message(conn, session_id, "user", "Мой ответ: 42", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "Верно", ts=1.0)

    assert notes.lesson_notes(conn) == []


def test_lesson_notes_brief_prefers_takeaway(conn) -> None:
    load_seed(conn)
    _add(
        conn,
        1.0,
        "Что такое groupby?",
        node="groupby",
        answer="Groupby разбивает строки по ключу.\n\nПример: df.groupby('x').sum().",
        takeaway="groupby делит строки по ключу, а агрегаты считает по группам.",
    )

    entry = notes.lesson_notes(conn)[0]["entries"][0]

    assert "groupby делит строки по ключу" in entry["brief_html"]
    assert "Пример:" in entry["answer_html"]
    assert "Пример:" not in entry["brief_html"]


def test_lesson_notes_brief_falls_back_to_first_paragraph(conn) -> None:
    load_seed(conn)
    _add(
        conn,
        1.0,
        "Что такое groupby?",
        node="groupby",
        answer="Groupby разбивает строки по ключу.\n\nВторой абзац про примеры.",
    )

    entry = notes.lesson_notes(conn)[0]["entries"][0]

    assert "Groupby разбивает строки по ключу." in entry["brief_html"]
    assert "Второй абзац" not in entry["brief_html"]


def test_lesson_notes_brief_is_clipped(conn) -> None:
    load_seed(conn)
    long_text = "Очень длинное объяснение без точек и границ предложений " * 10
    _add(conn, 1.0, "Что такое groupby?", node="groupby", answer=long_text, takeaway=long_text)

    brief = notes.lesson_notes(conn)[0]["entries"][0]["brief_html"]

    assert len(brief) < notes.BRIEF_CHARS + 100
    assert "…" in brief


def test_lesson_notes_kind_filter_by_quote_source(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Вопрос по теории", quote={"source": "theory", "text": "Кусок теории"})
    _add(conn, 2.0, "Вопрос по коду", quote={"source": "code", "text": "df.groupby('x')"})

    code = notes.lesson_notes(conn, kind="code")[0]["entries"]
    text = notes.lesson_notes(conn, kind="text")[0]["entries"]

    assert [entry["question"] for entry in code] == ["Вопрос по коду"]
    assert [entry["question"] for entry in text] == ["Вопрос по теории"]


def test_lesson_notes_failed_answer_has_no_brief(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Что тут?", node="groupby", answer=LLM_FAILURE_REPLY)

    entry = notes.lesson_notes(conn)[0]["entries"][0]

    assert entry["failed"] is True
    assert entry["brief_html"] is None
    assert entry["answer_html"] is None


# --- «Мои решения» ---


def test_solutions_find_answers_by_event_ts(conn) -> None:
    load_seed(conn)
    item = repos.get_items(conn)[0]

    _solution(conn, 1.0, "42", item)

    entries = notes.solution_entries(conn)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["is_code"] is False
    assert entry["right"] is True
    assert entry["verdict"] == "Верно"
    assert entry["node_id"] == main_concept(item)
    assert entry["prompt"]
    assert "42" in entry["answer_html"]


def test_solutions_parse_code_and_test_report(conn) -> None:
    load_seed(conn)
    item = repos.get_items(conn)[0]
    code = "print(sum([1, 2]))"

    _solution(
        conn,
        1.0,
        f"```python\n{code}\n```\n\nРезультат проверки: есть ошибки — AssertionError: ожидалось 3",
        item,
        result=0.0,
        source="autotest",
    )

    entry = notes.solution_entries(conn)[0]
    assert entry["is_code"] is True
    assert entry["code"] == code
    assert entry["passed"] is False
    assert "AssertionError: ожидалось 3" in entry["report"]
    assert entry["verdict"] == "Неверно"
    assert entry["answer_html"] is None


def test_solutions_skip_events_without_student_message(conn) -> None:
    load_seed(conn)
    item = repos.get_items(conn)[0]
    repos.add_event(
        conn,
        Event(
            source="checked",
            result=1.0,
            item_id=item.id,
            concept_id=main_concept(item),
            ts=1.0,
        ),
    )

    assert notes.solution_entries(conn) == []


def test_solutions_group_by_lesson_newest_first(conn) -> None:
    load_seed(conn)
    graph = CourseGraph.load(conn)
    items = {main_concept(item): item for item in repos.get_items(conn) if main_concept(item)}
    first, second = list(items.items())[:2]

    _solution(conn, 1.0, "старый ответ", first[1])
    _solution(conn, 2.0, "новый ответ", second[1])

    groups = notes.lesson_solutions(conn)
    assert [group["node_id"] for group in groups] == [second[0], first[0]]
    assert groups[0]["name"] == graph.concept(second[0]).name
    assert groups[0]["count"] == 1


def test_solutions_kind_filter(conn) -> None:
    load_seed(conn)
    items = repos.get_items(conn)

    _solution(conn, 1.0, "текстовый ответ", items[0])
    _solution(
        conn,
        2.0,
        "```python\nprint(1)\n```\n\nРезультат проверки: все проверки пройдены",
        items[-1],
        source="autotest",
    )

    newest = [entry["is_code"] for entry in notes.solution_entries(conn)]
    code = notes.lesson_solutions(conn, kind="code")[0]["entries"]
    text = notes.lesson_solutions(conn, kind="text")[0]["entries"]
    assert newest == [True, False]
    assert all(entry["is_code"] for entry in code)
    assert all(not entry["is_code"] for entry in text)


def test_solutions_kind_filter_can_empty_everything(conn) -> None:
    load_seed(conn)
    _solution(conn, 1.0, "текстовый ответ", repos.get_items(conn)[0])

    assert notes.lesson_solutions(conn, kind="code") == []


# --- страница ---


async def test_page_shows_question_fragment_and_answer(enrolled, web_app) -> None:
    await _finish(enrolled)
    state = (await enrolled.get(f"{BASE}/state")).json()["state"]
    node = state["node"]

    response = await enrolled.post(
        f"{BASE}/chat",
        json={
            "text": "Что такое groupby?",
            "quote": {"source": "theory", "text": "groupby делит строки"},
        },
    )
    assert response.status_code == 200, response.text
    html = (await enrolled.get("/notes")).text

    assert "Конспект" in html
    assert "Что такое groupby?" in html
    assert "groupby делит строки" in html
    assert "Теория" in html
    assert "Что вы узнали" in html
    assert TAKEAWAY in html
    assert "Полный ответ" in html
    assert REPLY in html
    assert node["name"] in html
    conn = _user_db(web_app)
    messages = repos.get_messages(conn, repos.get_open_session(conn))
    meta = json.loads([message for message in messages if message.role == "user"][-1].meta)
    assert meta["question"] is True and meta["node"] == node["id"]


async def test_page_hides_answers_and_plain_messages(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    session_id = repos.ensure_open_session(conn, 1.0)
    repos.add_message(conn, session_id, "user", "Мой ответ: 42", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "Верно", ts=1.0)

    html = (await enrolled.get("/notes")).text

    assert "Мой ответ: 42" not in html
    assert "Здесь появятся вопросы" in html


async def test_page_escapes_tutor_html(enrolled, web_app) -> None:
    _add(
        _user_db(web_app),
        1.0,
        "Что тут?",
        node="groupby",
        answer="<script>alert(1)</script>",
        takeaway="<script>alert(2)</script>",
    )

    html = (await enrolled.get("/notes")).text

    assert "<script>alert(1)</script>" not in html
    assert "<script>alert(2)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html


async def test_page_marks_failed_answer(enrolled, web_app) -> None:
    _add(
        _user_db(web_app),
        1.0,
        "Что тут?",
        node="groupby",
        answer=LLM_FAILURE_REPLY + "\n\nЗадание всё ещё ждёт ответа.",
    )

    html = (await enrolled.get("/notes")).text

    assert "Что тут?" in html
    assert "Ответ не получен" in html
    assert LLM_FAILURE_REPLY not in html


async def test_page_filter_by_course(student, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post("/api/courses/trees-demo/enroll", json={})
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    _add(web_app.state.userdbs.open(user.id, COURSE), 1.0, "Вопрос про pandas")
    _add(web_app.state.userdbs.open(user.id, "trees-demo"), 2.0, "Вопрос про деревья")

    all_html = (await student.get("/notes")).text
    filtered = (await student.get("/notes?course=trees-demo")).text
    unknown = (await student.get("/notes?course=nope")).text

    assert "Вопрос про pandas" in all_html and "Вопрос про деревья" in all_html
    assert "Вопрос про деревья" in filtered and "Вопрос про pandas" not in filtered
    assert "Вопрос про pandas" in unknown and "Вопрос про деревья" in unknown


async def test_page_filter_by_lesson_and_shows_topic_chips(student, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    conn = _user_db(web_app)
    _add(conn, 1.0, "Вопрос про группировку", node="l1_group")
    _add(conn, 2.0, "Вопрос про выборку", node="l1_select")

    html = (await student.get(f"/notes?course={COURSE}")).text
    assert "lesson=l1_group" in html and "lesson=l1_select" in html
    assert "Все темы" in html

    filtered = (await student.get(f"/notes?course={COURSE}&lesson=l1_group")).text
    assert "Вопрос про группировку" in filtered
    assert "Вопрос про выборку" not in filtered

    unknown = (await student.get(f"/notes?course={COURSE}&lesson=nope")).text
    assert "Вопрос про группировку" in unknown
    assert "Вопрос про выборку" in unknown


async def test_page_single_course_shows_topic_chips(student, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    _add(_user_db(web_app), 1.0, "Вопрос про группировку", node="l1_group")

    html = (await student.get("/notes")).text

    # Курс один — он выбран сразу, чипы тем видны без клика по курсу.
    assert "Все темы" in html
    assert "lesson=l1_group" in html


async def test_page_questions_kind_filter(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Вопрос по теории", quote={"source": "theory", "text": "Кусок теории"})
    _add(conn, 2.0, "Вопрос по коду", quote={"source": "code", "text": "df.groupby('x')"})

    code_html = (await enrolled.get("/notes?kind=code")).text
    text_html = (await enrolled.get("/notes?kind=text")).text

    assert "Вопрос по коду" in code_html and "Вопрос по теории" not in code_html
    assert "Вопрос по теории" in text_html and "Вопрос по коду" not in text_html


async def test_page_solutions_view_shows_answers(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _solution(conn, 1.0, "мой ответ на задание", repos.get_items(conn)[0])

    html = (await enrolled.get("/notes?view=solutions")).text

    assert "Мои решения" in html
    assert "мой ответ на задание" in html
    assert "Текст" in html
    assert "Верно" in html
    # Вкладка вопросов чужую ленту не показывает.
    assert "мой ответ на задание" not in (await enrolled.get("/notes")).text


async def test_page_solutions_kind_filter(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    items = repos.get_items(conn)
    _solution(conn, 1.0, "текстовый ответ", items[0])
    _solution(
        conn,
        2.0,
        "```python\nprint(1)\n```\n\nРезультат проверки: все проверки пройдены",
        items[-1],
        source="autotest",
    )

    code_html = (await enrolled.get("/notes?view=solutions&kind=code")).text
    text_html = (await enrolled.get("/notes?view=solutions&kind=text")).text

    assert "print(1)" in code_html and "текстовый ответ" not in code_html
    assert "текстовый ответ" in text_html and "print(1)" not in text_html


async def test_page_solutions_empty_state(enrolled) -> None:
    html = (await enrolled.get("/notes?view=solutions")).text

    assert "Здесь появятся ваши ответы на задания" in html
    assert "Мои решения" in html


async def test_page_solutions_kind_empty_state(enrolled, web_app) -> None:
    _solution(_user_db(web_app), 1.0, "текстовый ответ", repos.get_items(_user_db(web_app))[0])

    html = (await enrolled.get("/notes?view=solutions&kind=code")).text

    assert "Решений с кодом пока нет" in html


async def test_page_requires_login(web) -> None:
    response = await web.get("/notes")

    assert response.status_code == 303
    assert "/login" in response.headers["location"]


async def test_page_without_courses_offers_catalog(student) -> None:
    html = (await student.get("/notes")).text

    assert "Вы ещё не записаны ни на один курс" in html
    assert 'href="/courses"' in html
