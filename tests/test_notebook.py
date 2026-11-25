"""Личный конспект (ветка sanya): пометка вопросов и страница «Конспект» (2026-11-25).

В ветке sanya файл называется test_notes.py: имя занято заметками ветки golos.
"""

import json
import time

import pytest
from legacy_course import load_seed
from test_web_lesson import BASE, COURSE, _finish, _user_db
from web_fakes import ScriptedTutor

from llm_tutor.core.turn import post_turn
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import main_concept
from llm_tutor.db import repos
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY
from llm_tutor.schemas import (
    Event,
    ExplainReport,
    ExplainState,
    IdeaReport,
    PitfallReport,
    Route,
    RouteStep,
    SessionState,
)
from llm_tutor.web import accounts, notebook, notes

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
    """Реплика вопроса и ответ тьютора — как их пишет post_turn (один ts).

    ``takeaway`` — итог тьютора: ``post_turn`` кладёт его в ``meta`` реплики тьютора.
    """
    session_id = repos.ensure_open_session(conn, ts)
    meta: dict = {"question": True}
    if node:
        meta["node"] = node
    if section:
        meta["section"] = section
    if quote:
        meta["quote"] = quote
    repos.add_message(
        conn, session_id, "user", question, ts=ts, meta=json.dumps(meta, ensure_ascii=False)
    )
    answer_meta = json.dumps({"takeaway": takeaway}, ensure_ascii=False) if takeaway else None
    repos.add_message(conn, session_id, "assistant", answer, ts=ts, meta=answer_meta)


def _question_id(conn) -> int:
    """id последней реплики ученика — адрес записи конспекта для заметки."""
    return conn.execute(
        "SELECT id FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]


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


def test_lesson_notes_group_by_section(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Старый про groupby", node="groupby", section="group")
    _add(conn, 2.0, "Новый про сортировку", node="groupby", section="sort")
    _add(conn, 3.0, "Свежий про groupby", node="groupby", section="group")

    lesson = notes.lesson_notes(conn)[0]

    # Разделы — по первому (новому) входу, внутри — от новых к старым.
    assert [section["id"] for section in lesson["sections"]] == ["group", "sort"]
    assert [entry["question"] for entry in lesson["sections"][0]["entries"]] == [
        "Свежий про groupby",
        "Старый про groupby",
    ]
    assert [entry["question"] for entry in lesson["sections"][1]["entries"]] == [
        "Новый про сортировку"
    ]


def test_lesson_notes_skip_long_code_in_question(conn) -> None:
    load_seed(conn)
    lines = "\n".join(f"x{i} = {i}" for i in range(6))
    code = f"Посмотри мой код:\n\n```python\n{lines}\n```"
    _add(conn, 1.0, "Вопрос по теории")
    _add(conn, 2.0, code)

    entries = notes.lesson_notes(conn)[0]["entries"]

    # Длинный код в вопросе — разбор кода, а не определение.
    assert [entry["question"] for entry in entries] == ["Вопрос по теории"]


def test_lesson_notes_keep_small_code_in_question(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Почему `a + b` не возвращается?\n\n```python\na + b\n```")

    # Мелкий код (одна строка) определению не мешает.
    assert len(notes.lesson_notes(conn)[0]["entries"]) == 1


def test_code_lines_understands_indented_fence() -> None:
    code = "  ```python\n" + "\n".join(f"x{i} = {i}" for i in range(4)) + "\n  ```"

    assert notes.code_lines(code) == 4


def test_code_lines_allows_longer_closing_fence() -> None:
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(4)) + "\n`````"

    assert notes.code_lines(code) == 4


def test_lesson_notes_skip_indented_long_code(conn) -> None:
    load_seed(conn)
    lines = "\n".join(f"x{i} = {i}" for i in range(6))
    _add(conn, 1.0, f"Посмотри мой код:\n\n  ```python\n{lines}\n  ```")

    assert notes.lesson_notes(conn) == []


def test_lesson_notes_collapse_long_code_in_answer(conn) -> None:
    load_seed(conn)
    code = "\n".join(f"x{i} = {i}" for i in range(6))
    answer = f"Сначала текст.\n\n```python\n{code}\n```\n\nИ ещё текст."
    _add(conn, 1.0, "Что такое группа?", answer=answer)

    html = notes.lesson_notes(conn)[0]["entries"][0]["answer_html"]

    assert '<details class="note-code">' in html
    assert "Показать код" in html
    assert "Сначала текст." in html


def test_lesson_notes_keep_small_code_in_answer(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Что такое группа?", answer="Текст.\n\n```python\na + b\n```")

    html = notes.lesson_notes(conn)[0]["entries"][0]["answer_html"]

    assert 'class="note-code"' not in html
    assert "<pre>" in html


def test_lesson_notes_blank_lines_do_not_make_code_long(conn) -> None:
    load_seed(conn)
    # Две непустые строки и две пустые: это по-прежнему мелкий код.
    _add(conn, 1.0, "Что такое группа?", answer="Текст.\n\n```python\na = 1\n\n\nb = 2\n```")

    html = notes.lesson_notes(conn)[0]["entries"][0]["answer_html"]

    assert 'class="note-code"' not in html


def test_lesson_notes_search_by_question_answer_and_note(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Что такое groupby?", answer="Группировка строк.")
    _add(conn, 2.0, "Что такое merge?", answer="Склейка таблиц.")
    notes.save_note(conn, _question_id(conn), "Повторить merge", now=3.0)

    by_question = notes.lesson_notes(conn, query="groupby")[0]["entries"]
    by_answer = notes.lesson_notes(conn, query="склейка таблиц")[0]["entries"]
    by_note = notes.lesson_notes(conn, query="повторить")[0]["entries"]
    nothing = notes.lesson_notes(conn, query="нетакого")

    assert [entry["question"] for entry in by_question] == ["Что такое groupby?"]
    assert [entry["question"] for entry in by_answer] == ["Что такое merge?"]
    assert [entry["question"] for entry in by_note] == ["Что такое merge?"]
    assert nothing == []


def test_lesson_notes_filter_by_node_and_search(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Старый про groupby", node="groupby")
    _add(conn, 3.0, "Свежий про groupby", node="groupby")
    _add(conn, 4.0, "Самый новый про сортировку", node="sorting")

    # Поиск в выбранном занятии не подмешивает записи других занятий.
    by_node = notes.lesson_notes(conn, query="свежий", node_id="groupby")
    assert [entry["question"] for entry in by_node[0]["entries"]] == ["Свежий про groupby"]
    assert notes.lesson_notes(conn, query="свежий", node_id="sorting") == []

    # Лимит — после фильтра занятия: берётся свежее в группе, а не во всём курсе.
    limited = notes.lesson_notes(conn, limit=1, node_id="groupby")[0]["entries"]
    assert [entry["question"] for entry in limited] == ["Свежий про groupby"]


def test_lesson_notes_keep_quote_source_and_section(conn) -> None:
    load_seed(conn)
    _add(
        conn,
        1.0,
        "Почему так?",
        node="groupby",
        section="group",
        quote={"source": "task", "text": "  Условие задания"},
    )

    entry = notes.lesson_notes(conn)[0]["entries"][0]

    # API нормализует фрагмент перед записью; здесь он сохранён как есть.
    assert entry["quote"] == "  Условие задания"
    assert entry["source"] == "Задание"
    assert entry["section_id"] == "group"


def test_lesson_notes_skip_code_questions(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Вопрос по теории", quote={"source": "theory", "text": "Кусок теории"})
    _add(conn, 2.0, "Посмотри мой код", quote={"source": "code", "text": "def f(): ..."})

    entries = notes.lesson_notes(conn)[0]["entries"]

    # Разбор кода — не определение: в конспект-теорию он не попадает.
    assert [entry["question"] for entry in entries] == ["Вопрос по теории"]


def test_lesson_notes_skip_messages_without_question_marker(conn) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, 1.0)
    repos.add_message(conn, session_id, "user", "Мой ответ: 42", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "Верно", ts=1.0)

    assert notes.lesson_notes(conn) == []


def test_save_note_creates_and_updates(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")
    message_id = _question_id(conn)

    assert notes.save_note(conn, message_id, "  повторить группировку  ", now=2.0) == (
        "повторить группировку"
    )
    entry = notes.lesson_notes(conn)[0]["entries"][0]
    assert entry["note"] == "повторить группировку"

    notes.save_note(conn, message_id, "другая заметка", now=3.0)
    entry = notes.lesson_notes(conn)[0]["entries"][0]
    assert entry["note"] == "другая заметка"
    assert conn.execute("SELECT COUNT(*) FROM note_entries").fetchone()[0] == 1


def test_save_note_empty_deletes(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")
    message_id = _question_id(conn)
    notes.save_note(conn, message_id, "пометка", now=2.0)

    assert notes.save_note(conn, message_id, "   ", now=3.0) == ""
    assert notes.lesson_notes(conn)[0]["entries"][0]["note"] is None
    assert conn.execute("SELECT COUNT(*) FROM note_entries").fetchone()[0] == 0


def test_save_note_rejects_non_question(conn) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, 1.0)
    repos.add_message(conn, session_id, "user", "Мой ответ: 42", ts=1.0)

    with pytest.raises(LookupError):
        notes.save_note(conn, _question_id(conn), "заметка", now=2.0)


def test_save_note_rejects_too_long(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")

    with pytest.raises(ValueError):
        notes.save_note(
            conn,
            _question_id(conn),
            "я" * (notes.NOTE_MAX_CHARS + 1),
            now=2.0,
        )


def test_lesson_notes_failed_answer_has_no_answer(conn) -> None:
    load_seed(conn)
    _add(conn, 1.0, "Что тут?", node="groupby", answer=LLM_FAILURE_REPLY)

    entry = notes.lesson_notes(conn)[0]["entries"][0]

    assert entry["failed"] is True
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
    assert entry["prompt_html"]
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


def test_solutions_show_full_rendered_prompt(conn) -> None:
    load_seed(conn)
    item = repos.get_items(conn)[0]
    prompt = "**Длинное условие** " + "слово " * 80 + "\n\n```python\nx = 1\n```"
    conn.execute("UPDATE items SET prompt = ? WHERE id = ?", (prompt, item.id))
    conn.commit()

    _solution(conn, 1.0, "мой ответ", item)
    entry = notes.solution_entries(conn)[0]

    # Условие не обрезается и проходит безопасный Markdown, как в занятии.
    assert "<strong>Длинное условие</strong>" in entry["prompt_html"]
    assert "слово слово" in entry["prompt_html"]
    assert "<pre>" in entry["prompt_html"]
    assert "…" not in entry["prompt_html"]


def test_solutions_search_by_prompt_and_answer(conn) -> None:
    load_seed(conn)
    first, second = repos.get_items(conn)[:2]
    conn.execute("UPDATE items SET prompt = ? WHERE id = ?", ("Особое условие", first.id))
    conn.commit()
    _solution(conn, 1.0, "мой ответ", first)
    _solution(conn, 2.0, "другой ответ", second)

    by_prompt = notes.solution_entries(conn, query="особое условие")
    by_answer = notes.solution_entries(conn, query="другой")

    assert [entry["item_id"] for entry in by_prompt] == [first.id]
    assert [entry["item_id"] for entry in by_answer] == [second.id]


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
    assert "Определения и пояснения" not in html  # лента заменена скелетом курса (срез 107)
    assert "Что такое groupby?" in html
    assert "groupby делит строки" in html
    assert "Теория" in html
    assert "Моя заметка" in html
    assert "Добавить заметку" in html
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
    assert "Конспект заполняется сам" in html


async def test_page_escapes_tutor_html(enrolled, web_app) -> None:
    _add(
        _user_db(web_app),
        1.0,
        "Что тут?",
        node="groupby",
        answer="<script>alert(1)</script>",
    )

    html = (await enrolled.get("/notes")).text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


async def test_page_shows_personal_note_escaped(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")
    notes.save_note(conn, _question_id(conn), "<b>моя</b> заметка", now=2.0)

    html = (await enrolled.get("/notes")).text

    assert "Изменить" in html
    assert "&lt;b&gt;моя&lt;/b&gt; заметка" in html
    assert "<b>моя</b> заметка" not in html
    assert 'maxlength="2000"' in html


async def test_page_note_cannot_close_textarea(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")
    notes.save_note(
        conn,
        _question_id(conn),
        "</textarea><script>alert(1)</script>",
        now=2.0,
    )

    html = (await enrolled.get("/notes")).text

    assert "</textarea><script>" not in html
    assert "&lt;/textarea&gt;" in html


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

    all_html = (await student.get("/notes?all=1")).text
    filtered = (await student.get(f"/notes?course={COURSE}")).text
    unknown = (await student.get("/notes?course=nope")).text

    assert "Вопрос про pandas" in all_html and "Вопрос про деревья" in all_html
    assert "Вопрос про pandas" in filtered and "Вопрос про деревья" not in filtered
    # Неизвестный курс игнорируется: выбран текущий (последний записанный).
    assert "Вопрос про деревья" in unknown and "Вопрос про pandas" not in unknown


async def test_page_filter_by_lesson_and_shows_topic_chips(student, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    conn = _user_db(web_app)
    _add(conn, 1.0, "Вопрос про группировку", node="grouping")
    _add(conn, 2.0, "Вопрос про выборку", node="selection")

    html = (await student.get(f"/notes?course={COURSE}")).text
    assert "lesson=grouping" in html and "lesson=selection" in html
    assert "Все темы" in html

    filtered = (await student.get(f"/notes?course={COURSE}&lesson=grouping")).text
    assert "Вопрос про группировку" in filtered
    assert "Вопрос про выборку" not in filtered

    unknown = (await student.get(f"/notes?course={COURSE}&lesson=nope")).text
    assert "Вопрос про группировку" in unknown
    assert "Вопрос про выборку" in unknown


async def test_page_single_course_shows_topic_chips(student, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    _add(_user_db(web_app), 1.0, "Вопрос про группировку", node="grouping")

    html = (await student.get("/notes")).text

    # Курс один — он выбран сразу, чипы тем видны без клика по курсу.
    assert "Все темы" in html
    assert "lesson=grouping" in html


async def test_page_shows_theory_and_hides_code_questions(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Вопрос по теории", quote={"source": "theory", "text": "Кусок теории"})
    _add(conn, 2.0, "Посмотри мой код", quote={"source": "code", "text": "def f(): ..."})

    html = (await enrolled.get("/notes?kind=code")).text

    # Старые закладки с фильтром типа страницу не ломают: фильтра больше нет.
    # Разбор кода в «Определениях и пояснениях» не показывается.
    assert "Вопрос по теории" in html
    assert "Посмотри мой код" not in html


async def test_page_solutions_view_shows_all_answers(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    items = repos.get_items(conn)
    _solution(conn, 1.0, "мой ответ на задание", items[0])
    _solution(conn, 2.0, "неверный ответ", items[1], result=0.0)
    _solution(
        conn,
        3.0,
        "```python\nprint(1)\n```\n\nРезультат проверки: все проверки пройдены",
        items[-1],
        source="autotest",
    )

    html = (await enrolled.get("/notes?view=solutions")).text

    assert "Выполненные задания" in html
    assert "мой ответ на задание" in html and "Верно" in html
    assert "неверный ответ" in html and "Неверно" in html
    assert "print(1)" in html
    # Тип записи больше не бейджем: лента и так делит пояснения и задания.
    assert ">Текст<" not in html
    # Лента пояснений чужую ленту не показывает.
    assert "мой ответ на задание" not in (await enrolled.get("/notes")).text


async def test_page_solutions_empty_state(enrolled) -> None:
    html = (await enrolled.get("/notes?view=solutions")).text

    assert "Здесь появятся выполненные задания" in html
    assert "Выполненные задания" in html


async def test_page_search_filters_entries(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?", answer="Группировка строк.")
    _add(conn, 2.0, "Что такое merge?", answer="Склейка таблиц.")

    html = (await enrolled.get("/notes?q=склейка")).text

    assert "Что такое merge?" in html
    assert "Что такое groupby?" not in html
    assert 'value="склейка"' in html


async def test_page_search_finds_note(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?")
    notes.save_note(conn, _question_id(conn), "повторить merge", now=2.0)

    html = (await enrolled.get("/notes?q=merge")).text

    assert "Что такое groupby?" in html
    # Запрос сохраняется в ссылках-чипах: смена ленты не теряет поиск.
    assert "&amp;q=merge" in html


async def test_page_search_empty_state(enrolled) -> None:
    html = (await enrolled.get("/notes?q=нетакого")).text

    assert "По запросу «нетакого» ничего не найдено" in html
    assert "Сбросить поиск" in html


async def test_page_search_keeps_lesson_filter(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Вопрос про группировку", node="grouping")
    _add(conn, 2.0, "Вопрос про выборку", node="selection")

    html = (await enrolled.get(f"/notes?course={COURSE}&lesson=grouping&q=выборку")).text

    # Поиск идёт внутри выбранной темы, а не по всему курсу.
    assert "Вопрос про выборку" not in html
    assert "ничего не найдено" in html


async def test_page_search_unknown_lesson_is_ignored(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Вопрос про группировку", node="l1_group")
    _add(conn, 2.0, "Вопрос про выборку", node="l1_select")

    html = (await enrolled.get(f"/notes?course={COURSE}&lesson=nope&q=выборку")).text

    assert "Вопрос про выборку" in html


async def test_page_escapes_search_query(enrolled) -> None:
    html = (await enrolled.get("/notes?q=<script>alert(1)</script>")).text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


async def test_page_search_in_solutions(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    first, second = repos.get_items(conn)[:2]
    conn.execute("UPDATE items SET prompt = ? WHERE id = ?", ("Особое условие", first.id))
    conn.commit()
    _solution(conn, 1.0, "мой ответ", first)
    _solution(conn, 2.0, "другой ответ", second)

    html = (await enrolled.get("/notes?view=solutions&q=особое")).text

    assert "Особое условие" in html
    assert "другой ответ" not in html


async def test_page_groups_entries_by_section(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    graph = CourseGraph.load(conn)
    concept = next(c for c in (graph.concept(node) for node in graph.node_ids) if c.sections)
    section = concept.sections[0]
    _add(conn, 1.0, "Вопрос по разделу", node=concept.id, section=section.id)

    html = (await enrolled.get("/notes")).text

    assert 'class="notes-section"' in html
    assert section.title in html


# --- API заметки ---


async def test_api_saves_and_deletes_note(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")
    url = f"/api/courses/{COURSE}/notebook/{_question_id(conn)}"

    response = await enrolled.put(url, json={"text": "  повторить groupby  "})
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "повторить groupby"}
    assert "повторить groupby" in (await enrolled.get("/notes")).text

    response = await enrolled.put(url, json={"text": ""})
    assert response.status_code == 200, response.text
    assert response.json() == {"text": ""}
    html = (await enrolled.get("/notes")).text
    assert "повторить groupby" not in html
    assert "Добавить заметку" in html


async def test_page_note_endpoint_is_not_shadowed_by_fragment_notes(enrolled, web_app) -> None:
    """Слияние sanya в golos-admin: ``PUT …/notes/{id}`` занят заметками-фрагментами.

    Роутер ``api_lesson`` подключён раньше ``api_notes``, поэтому на старом
    адресе запрос ушёл бы к фрагментам и заметка конспекта не сохранилась бы.
    Страница обязана давать адрес ``notebook``, и он должен сохранять заметку.
    """
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")
    message_id = _question_id(conn)
    endpoint = f"/api/courses/{COURSE}/notebook/{message_id}"

    assert f'data-note-endpoint="{endpoint}"' in (await enrolled.get("/notes")).text
    response = await enrolled.put(endpoint, json={"text": "своё определение"})
    assert response.status_code == 200, response.text
    assert "своё определение" in (await enrolled.get("/notes")).text
    # Старый адрес отвечает обработчик фрагментов: заметки-фрагмента с таким id
    # нет — его 404 «Заметки нет.», а не сохранение заметки конспекта.
    old = await enrolled.put(
        f"/api/courses/{COURSE}/notes/{message_id}", json={"text": "чужое"}
    )
    assert old.status_code == 404
    assert old.json()["detail"]["message"] == "Заметки нет."
    assert "чужое" not in (await enrolled.get("/notes")).text


async def test_api_note_rejects_non_question(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    session_id = repos.ensure_open_session(conn, 1.0)
    repos.add_message(conn, session_id, "user", "Мой ответ: 42", ts=1.0)

    response = await enrolled.put(
        f"/api/courses/{COURSE}/notebook/{_question_id(conn)}", json={"text": "заметка"}
    )

    assert response.status_code == 404


async def test_api_note_rejects_assistant_message(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")
    assistant_id = conn.execute(
        "SELECT id FROM messages WHERE role = 'assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]

    response = await enrolled.put(
        f"/api/courses/{COURSE}/notebook/{assistant_id}", json={"text": "заметка"}
    )

    assert response.status_code == 404


async def test_api_note_rejects_huge_id(enrolled) -> None:
    response = await enrolled.put(
        f"/api/courses/{COURSE}/notebook/999999999999999999999", json={"text": "заметка"}
    )

    assert response.status_code == 404


async def test_api_note_rejects_long_text(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    _add(conn, 1.0, "Что такое groupby?", node="groupby")

    response = await enrolled.put(
        f"/api/courses/{COURSE}/notebook/{_question_id(conn)}",
        json={"text": "я" * (notes.NOTE_MAX_CHARS + 1)},
    )

    assert response.status_code == 422


async def test_api_note_requires_login(web) -> None:
    response = await web.put(f"/api/courses/{COURSE}/notebook/1", json={"text": "заметка"})

    assert response.status_code == 401


async def test_api_note_requires_enrollment(student) -> None:
    response = await student.put(f"/api/courses/{COURSE}/notebook/1", json={"text": "заметка"})

    assert response.status_code == 403


async def test_page_requires_login(web) -> None:
    response = await web.get("/notes")

    assert response.status_code == 303
    assert "/login" in response.headers["location"]


async def test_page_without_courses_offers_catalog(student) -> None:
    html = (await student.get("/notes")).text

    assert "Вы ещё не записаны ни на один курс" in html
    assert 'href="/courses"' in html


# --- скелет курса (срез 107) ---


def _route(
    conn,
    *,
    closed: tuple[str, ...] = (),
    weak: tuple[str, ...] = (),
    reopened: tuple[str, ...] = (),
    current: str | None = None,
    passed_sections: tuple[str, ...] = (),
    section: str | None = None,
    closed_at: float = 1.0,
) -> None:
    """Пишет снимок маршрута в открытую сессию: закрытые, переоткрытые и текущее занятие."""
    steps = [
        RouteStep(concept_id=node, mode="full", status="closed", closed_at=closed_at)
        for node in closed
    ]
    steps += [
        RouteStep(concept_id=node, mode="full", status="closed", closed_at=closed_at, weak=True)
        for node in weak
    ]
    steps += [
        RouteStep(concept_id=node, mode="full", status="ahead", closed_at=closed_at)
        for node in reopened
    ]
    if current is not None:
        steps.append(RouteStep(concept_id=current, mode="full", status="current"))
    session_id = repos.ensure_open_session(conn, 1.0)
    state = repos.get_session_state(conn, session_id).model_copy(
        update={
            "route": Route(steps=steps),
            "current_node_id": current,
            "passed_sections": list(passed_sections),
            "section_id": section,
        }
    )
    repos.update_session_state(conn, session_id, state)
    conn.commit()


def _order(conn) -> list[str]:
    return CourseGraph.load(conn).topo_order()


async def test_skeleton_orders_lessons_and_marks_statuses(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    order = _order(conn)
    _route(conn, closed=(order[0],), weak=(order[1],), current=order[2])

    book = notebook.course_notebook(conn)

    assert [lesson["node_id"] for lesson in book["lessons"]] == order[:3]
    assert [lesson["status"] for lesson in book["lessons"]] == ["passed", "weak", "current"]
    graph = CourseGraph.load(conn)
    assert book["ahead"] == [graph.concept(node).name for node in order[3:]]
    first = book["lessons"][0]
    assert [section["title"] for section in first["sections"]] == [
        s.title for s in graph.concept(order[0]).sections
    ]
    assert all(section["passed"] and section["gist_html"] for section in first["sections"])
    assert book["closed"] == 2 and book["total"] == len(order)


async def test_current_lesson_passed_and_current_sections_have_gist(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    sections = CourseGraph.load(conn).concept(node).sections
    assert len(sections) >= 3
    _route(conn, current=node, passed_sections=(sections[0].id,), section=sections[1].id)

    lesson = notebook.course_notebook(conn)["lessons"][0]

    passed = [(section["passed"], bool(section["gist_html"])) for section in lesson["sections"]]
    assert passed[:3] == [(True, True), (True, True), (False, False)]


async def test_reopened_lesson_keeps_gist(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    _route(conn, reopened=(node,))

    lesson = notebook.course_notebook(conn)["lessons"][0]

    assert lesson["status"] == "reopened"
    assert all(section["gist_html"] for section in lesson["sections"])


async def test_pending_explain_lesson_is_locked(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    important = next(node for node in _order(conn) if node in repos.get_explain_configs(conn))
    _route(conn, closed=(important,), closed_at=time.time() + 60)

    book = notebook.course_notebook(conn)
    lesson = next(item for item in book["lessons"] if item["node_id"] == important)
    html = (await enrolled.get("/notes")).text

    assert lesson["locked"] is True
    assert lesson["sections"] == []
    assert notebook.LOCKED_NOTE in html


async def test_no_route_everything_ahead(enrolled, web_app) -> None:
    conn = _user_db(web_app)

    book = notebook.course_notebook(conn)
    response = await enrolled.get("/notes")

    assert book["lessons"] == []
    assert len(book["ahead"]) == len(_order(conn))
    assert response.status_code == 200
    assert "Впереди" in response.text


async def test_section_without_theory_heading_has_no_gist(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    conn.execute("UPDATE concepts SET theory = ? WHERE id = ?", ("# Занятие\n\nБез разделов.", node))
    conn.commit()
    _route(conn, closed=(node,))

    lesson = notebook.course_notebook(conn)["lessons"][0]
    response = await enrolled.get("/notes")

    assert lesson["sections"] and all(s["gist_html"] is None for s in lesson["sections"])
    assert response.status_code == 200


async def test_page_renders_skeleton(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    order = _order(conn)
    graph = CourseGraph.load(conn)
    sections = graph.concept(order[0]).sections
    _route(conn, current=order[0], passed_sections=(sections[0].id,), section=sections[0].id)

    html = (await enrolled.get("/notes")).text

    assert "Конспект" in html and "Выполненные задания" in html
    assert sections[0].title in html
    assert f"{sections[-1].title} — ещё впереди" in html
    assert "идёт сейчас" in html
    assert graph.concept(order[-1]).name in html
    assert 'class="gist"' in html


async def test_old_view_questions_is_notebook(enrolled, web_app) -> None:
    _route(_user_db(web_app), closed=(_order(_user_db(web_app))[0],))

    old = (await enrolled.get("/notes?view=questions")).text
    new = (await enrolled.get("/notes")).text

    assert 'class="gist"' in old and 'class="gist"' in new


async def test_default_course_is_current_and_all_has_no_skeleton(student, web_app) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post("/api/courses/trees-demo/enroll", json={})
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    _add(web_app.state.userdbs.open(user.id, COURSE), 1.0, "Вопрос про pandas", node="grouping")
    _add(web_app.state.userdbs.open(user.id, "trees-demo"), 2.0, "Вопрос про деревья")

    default = (await student.get("/notes")).text
    every = (await student.get("/notes?all=1")).text

    # Последний записанный курс — текущий: его скелет, чужих вопросов нет.
    assert "Вопрос про деревья" in default and "Вопрос про pandas" not in default
    assert "Впереди" in default
    assert "Вопрос про pandas" in every and "Вопрос про деревья" in every
    assert "Впереди" not in every and "ещё впереди" not in every


# --- вопросы тьютору в конспекте (срез 108) ---


def _first_section(conn) -> tuple[str, str]:
    node = _order(conn)[0]
    return node, CourseGraph.load(conn).concept(node).sections[0].id


async def test_question_shows_takeaway_as_gist(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    _add(
        conn,
        1.0,
        "Чем энтропия отличается от Джини?",
        node=node,
        section=section,
        answer="Длинный разбор с примерами и формулами.",
        takeaway="Джини считается **без логарифма**.",
    )

    lesson = notebook.course_notebook(conn)["lessons"][0]
    entry = lesson["sections"][0]["questions"][0]
    html = (await enrolled.get("/notes")).text

    assert str(entry["gist_html"]).strip() == "<p>Джини считается <strong>без логарифма</strong>.</p>"
    assert "Джини считается <strong>без логарифма</strong>." in html
    assert "Длинный разбор с примерами и формулами." in html  # полный ответ — под спойлером
    assert 'class="note-gist"' in html


async def test_question_without_takeaway_uses_first_paragraph(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    _add(
        conn,
        1.0,
        "Что такое лист?",
        node=node,
        section=section,
        answer="Лист хранит самый частый класс среди объектов.\n\nВторой абзац с подробностями.",
    )

    entry = notebook.course_notebook(conn)["lessons"][0]["sections"][0]["questions"][0]

    assert "Лист хранит самый частый класс" in str(entry["gist_html"])
    assert "Второй абзац" not in str(entry["gist_html"])


async def test_question_without_section_goes_to_lesson_block(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    _add(conn, 1.0, "Общий вопрос по занятию", node=node, takeaway="Итог по занятию.")

    lesson = notebook.course_notebook(conn)["lessons"][0]
    html = (await enrolled.get("/notes")).text

    assert [entry["question"] for entry in lesson["questions"]] == ["Общий вопрос по занятию"]
    assert lesson["status"] == "ahead"  # занятие с материалом показывается целиком
    assert "По занятию целиком" in html


async def test_takeaway_html_is_escaped(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    _add(conn, 1.0, "Вопрос", node=node, section=section, takeaway="<script>alert(1)</script>")

    html = (await enrolled.get("/notes")).text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


async def test_failed_answer_has_no_gist(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    _add(conn, 1.0, "Вопрос без ответа", node=node, section=section, answer=LLM_FAILURE_REPLY)

    entry = notebook.course_notebook(conn)["lessons"][0]["sections"][0]["questions"][0]
    html = (await enrolled.get("/notes")).text

    assert entry["gist_html"] is None and entry["failed"] is True
    assert "Ответ не получен" in html


async def test_search_finds_question_by_takeaway(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    _add(conn, 1.0, "Вопрос А", node=node, section=section, answer="Ответ.", takeaway="про бутстрэп")
    _add(conn, 2.0, "Вопрос Б", node=node, section=section, answer="Ответ.", takeaway="про Джини")

    html = (await enrolled.get("/notes?q=бутстрэп")).text

    assert "Вопрос А" in html and "Вопрос Б" not in html


# --- фрагменты в конспекте (срез 109) ---


async def test_fragment_in_its_section_with_comment(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    repos.add_note(
        conn, concept_id=node, source="theory", quote="цитата из раздела", comment="моё", section_id=section
    )

    lesson = notebook.course_notebook(conn)["lessons"][0]
    fragment = lesson["sections"][0]["fragments"][0]
    html = (await enrolled.get("/notes")).text

    assert fragment["quote"] == "цитата из раздела" and fragment["comment"] == "моё"
    assert fragment["source"] == "Теория"
    assert "цитата из раздела" in html and "моё" in html
    assert f'data-delete-note="{fragment["id"]}"' in html
    assert 'class="note-comment-form"' in html


async def test_fragment_lesson_level_and_outside(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    repos.add_note(conn, concept_id=node, source="task", quote="без раздела", section_id="нет-такого")
    repos.add_note(conn, concept_id=None, source="theory", quote="без занятия")

    book = notebook.course_notebook(conn)
    lesson = next(item for item in book["lessons"] if item["node_id"] == node)

    assert [item["quote"] for item in lesson["fragments"]] == ["без раздела"]
    assert [item["quote"] for item in book["outside"]["fragments"]] == ["без занятия"]
    assert "без занятия" in (await enrolled.get("/notes")).text


async def test_all_fragments_shown_beyond_100(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    for number in range(101):
        repos.add_note(
            conn, concept_id=node, source="theory", quote=f"фрагмент {number}", section_id=section, now=float(number)
        )

    lesson = notebook.course_notebook(conn)["lessons"][0]

    assert len(lesson["sections"][0]["fragments"]) == 101
    # По порядку сохранения: конспект читается сверху вниз.
    assert lesson["sections"][0]["fragments"][0]["quote"] == "фрагмент 0"


async def test_fragment_quote_escaped(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    repos.add_note(
        conn, concept_id=node, source="theory", quote="<img src=x onerror=alert(1)>", section_id=section,
        comment="<b>жирно</b>",
    )

    html = (await enrolled.get("/notes")).text

    assert "<img src=x" not in html and "<b>жирно</b>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


async def test_locked_lesson_hides_fragments(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    important = next(node for node in _order(conn) if node in repos.get_explain_configs(conn))
    repos.add_note(conn, concept_id=important, source="theory", quote="подсказка к объяснению")
    _route(conn, closed=(important,), closed_at=time.time() + 60)

    assert "подсказка к объяснению" not in (await enrolled.get("/notes")).text


async def test_search_finds_fragment_by_comment(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, section = _first_section(conn)
    repos.add_note(conn, concept_id=node, source="theory", quote="первая", comment="про EDA", section_id=section)
    repos.add_note(conn, concept_id=node, source="theory", quote="вторая", section_id=section)

    html = (await enrolled.get("/notes?q=eda")).text

    assert "первая" in html and "вторая" not in html


# --- загвоздки: ошибки в заданиях (срез 110) ---


def _lesson_items(conn, node: str) -> list:
    return [item for item in repos.get_items(conn) if main_concept(item) == node]


def _lesson_of(book: dict, node: str) -> dict:
    return next(lesson for lesson in book["lessons"] if lesson["node_id"] == node)


def _section_of(lesson: dict, section_id: str) -> dict:
    return next(section for section in lesson["sections"] if section["id"] == section_id)


async def test_wrong_choice_shows_given_and_correct(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    item = next(i for i in _lesson_items(conn, node) if i.answer_type == "choice" and i.section)
    right = int(item.answer)
    wrong = (right + 1) % len(item.options)
    _solution(conn, 1.0, item.options[wrong], item, result=0.0)

    lesson = _lesson_of(notebook.course_notebook(conn), node)
    mistake = _section_of(lesson, item.section)["mistakes"][0]
    html = (await enrolled.get("/notes")).text

    assert mistake["item_id"] == item.id
    assert mistake["given"].startswith(item.options[wrong][:20])
    assert mistake["correct"].startswith(item.options[right][:20])
    assert mistake["solved_later"] is False
    assert "Ошибка" in html and "Ваш ответ" in html and "Верно" in html


async def test_mistake_later_solved_marked(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    item = next(i for i in _lesson_items(conn, node) if i.answer_type == "choice" and i.section)
    _solution(conn, 1.0, "неверно", item, result=0.0)
    _solution(conn, 2.0, item.options[int(item.answer)], item, result=1.0)

    mistake = _section_of(_lesson_of(notebook.course_notebook(conn), node), item.section)["mistakes"][0]

    assert mistake["solved_later"] is True
    assert "потом решено верно" in (await enrolled.get("/notes")).text


async def test_one_entry_per_item_latest_wrong(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    item = next(i for i in _lesson_items(conn, node) if i.answer_type == "choice" and i.section)
    _solution(conn, 1.0, "первая попытка", item, result=0.0)
    _solution(conn, 2.0, "вторая попытка", item, result=0.0)
    # Ядро пишет по событию на концепт задания с одним ts.
    repos.add_event(conn, Event(source="checked", result=0.0, item_id=item.id, concept_id=node, ts=2.0))

    mistakes = _section_of(_lesson_of(notebook.course_notebook(conn), node), item.section)["mistakes"]

    assert len(mistakes) == 1
    assert mistakes[0]["given"] == "вторая попытка"


async def test_final_item_mistake_on_lesson_level(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    item = next(i for i in _lesson_items(conn, node) if i.final)
    _solution(conn, 1.0, "что-то не то", item, result=0.0)

    lesson = _lesson_of(notebook.course_notebook(conn), node)

    assert [m["item_id"] for m in lesson["mistakes"]] == [item.id]
    assert all(not section["mistakes"] for section in lesson["sections"])


async def test_code_mistake_without_reference(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    item = next(i for i in _lesson_items(conn, node) if i.tests and i.section and not i.final)
    _solution(
        conn,
        1.0,
        "```python\nresult = 1\n```\n\nРезультат проверки: не пройдено — AssertionError",
        item,
        result=0.0,
        source="autotest",
    )

    mistake = _section_of(_lesson_of(notebook.course_notebook(conn), node), item.section)["mistakes"][0]

    assert mistake["given"] == "код не прошёл проверку"
    assert mistake["correct"] is None


async def test_open_item_mistake_shows_reference(enrolled, web_app) -> None:
    # Эталон есть у коротких ответов (short); открытые (open) проверяет рубрика.
    conn = _user_db(web_app)
    item = next(
        i for i in repos.get_items(conn)
        if i.answer_type not in ("choice", "code") and i.section and i.answer
    )
    node = main_concept(item)
    _solution(conn, 1.0, "мой открытый ответ", item, result=0.2, source="rubric")

    mistake = _section_of(_lesson_of(notebook.course_notebook(conn), node), item.section)["mistakes"][0]

    assert mistake["given"] == "мой открытый ответ"
    assert mistake["correct"] and item.answer.split()[0] in mistake["correct"]


async def test_explain_events_are_not_mistakes(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    item = next(i for i in _lesson_items(conn, node) if i.answer_type == "choice" and i.section)
    _solution(conn, 1.0, "объяснение", item, result=0.0, source="explain")

    assert notebook.task_mistakes(conn) == []


async def test_missing_item_event_skipped(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node = _order(conn)[0]
    item = next(i for i in _lesson_items(conn, node) if i.answer_type == "choice" and i.section)
    _solution(conn, 1.0, "ответ", item, result=0.0)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("UPDATE events SET item_id = 999999 WHERE item_id = ?", (item.id,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    response = await enrolled.get("/notes")

    assert notebook.task_mistakes(conn) == []
    assert response.status_code == 200


async def test_locked_lesson_hides_mistakes(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    important = next(node for node in _order(conn) if node in repos.get_explain_configs(conn))
    item = next(i for i in _lesson_items(conn, important) if i.answer_type == "choice")
    _solution(conn, 1.0, "неверный выбор", item, result=0.0)
    _route(conn, closed=(important,), closed_at=time.time() + 60)

    assert "неверный выбор" not in (await enrolled.get("/notes")).text


# --- загвоздки: «Объясни новичку» (срез 111) ---


def _important(conn, index: int = 0) -> tuple[str, object]:
    configs = repos.get_explain_configs(conn)
    node = [node for node in _order(conn) if node in configs][index]
    return node, configs[node]


def _explain_session(
    conn,
    *,
    concept_id: str | None,
    ideas: list,
    pitfalls: list = (),
    ended_at: float = 10.0,
    status: str = "finished",
) -> None:
    session_id = repos.create_explain_session(conn, 1, now=ended_at - 1, concept_id=concept_id, kind="required")
    report = ExplainReport(
        topic_id=1,
        required_done=0,
        required_total=1,
        ideas_done=0,
        ideas_total=len(ideas),
        ideas=list(ideas),
        pitfalls=list(pitfalls),
        end_reason="button",
    )
    repos.update_explain_session(
        conn,
        session_id,
        state=ExplainState(),
        updated_at=ended_at,
        status=status,
        ended_at=ended_at if status == "finished" else None,
        report=report if status == "finished" else None,
        commit=True,
    )


async def test_explain_wrong_idea_shown(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, config = _important(conn)
    idea = next(i for i in config.ideas if i.required)
    _explain_session(
        conn, concept_id=node, ideas=[IdeaReport(id=idea.id, lesson=node, state="wrong", reference=idea.text)]
    )

    lesson = _lesson_of(notebook.course_notebook(conn), node)

    assert lesson["explain"] == [{"kind": "wrong", "label": "Ошибся, объясняя:", "text": idea.text, "belief": None}]
    assert "Ошибся, объясняя:" in (await enrolled.get("/notes")).text


async def test_explain_missing_required_only(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, config = _important(conn)
    required = next(i for i in config.ideas if i.required)
    optional = [i for i in config.ideas if not i.required]
    reports = [IdeaReport(id=required.id, lesson=node, state="missing", reference=required.text)]
    reports += [IdeaReport(id=i.id, lesson=node, state="missing", reference=i.text) for i in optional]
    reports.append(IdeaReport(id="ушла-из-эталона", lesson=node, state="partial", reference="старая идея"))
    _explain_session(conn, concept_id=node, ideas=reports)

    explain = _lesson_of(notebook.course_notebook(conn), node)["explain"]

    assert [(e["kind"], e["text"]) for e in explain] == [("missed", required.text)]


async def test_explain_missed_pitfall_with_belief(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, config = _important(conn)
    pitfall = config.pitfalls[0]
    _explain_session(
        conn,
        concept_id=node,
        ideas=[],
        pitfalls=[PitfallReport(id=pitfall.id, lesson=node, outcome="missed", fix=pitfall.fix)],
    )

    explain = _lesson_of(notebook.course_notebook(conn), node)["explain"]
    html = (await enrolled.get("/notes")).text

    assert explain == [
        {"kind": "pitfall", "label": "Не заметил ловушку:", "text": pitfall.fix, "belief": pitfall.belief}
    ]
    assert "Не заметил ловушку:" in html


async def test_explain_latest_session_wins(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, config = _important(conn)
    idea = next(i for i in config.ideas if i.required)
    _explain_session(
        conn, concept_id=node, ended_at=10.0,
        ideas=[IdeaReport(id=idea.id, lesson=node, state="wrong", reference=idea.text)],
    )
    _explain_session(conn, concept_id=node, ended_at=20.0, ideas=[IdeaReport(id=idea.id, lesson=node, state="correct")])

    book = notebook.course_notebook(conn)

    assert all(lesson["node_id"] != node or not lesson["explain"] for lesson in book["lessons"])


async def test_old_module_session_split_by_idea_lesson(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    first, first_config = _important(conn, 0)
    second, second_config = _important(conn, 1)
    a = next(i for i in first_config.ideas if i.required)
    b = next(i for i in second_config.ideas if i.required)
    _explain_session(
        conn,
        concept_id=None,
        ideas=[
            IdeaReport(id=a.id, lesson=first, state="wrong", reference=a.text),
            IdeaReport(id=b.id, lesson=second, state="wrong", reference=b.text),
        ],
    )

    book = notebook.course_notebook(conn)

    assert [e["text"] for e in _lesson_of(book, first)["explain"]] == [a.text]
    assert [e["text"] for e in _lesson_of(book, second)["explain"]] == [b.text]


async def test_active_explain_session_ignored(enrolled, web_app) -> None:
    conn = _user_db(web_app)
    node, config = _important(conn)
    idea = next(i for i in config.ideas if i.required)
    _explain_session(
        conn, concept_id=node, status="active",
        ideas=[IdeaReport(id=idea.id, lesson=node, state="wrong", reference=idea.text)],
    )

    assert notebook.explain_snags(conn) == {}
