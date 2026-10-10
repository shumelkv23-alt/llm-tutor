"""Регрессии по находкам ревью срезов 46–47.

Данные и проверки заданий — в test_seed_code_items (WRONG, ALTERNATIVES),
гонки вкладки «Код» — в tests/e2e/test_audit_46_47_code.py.
"""

import re

import pytest
from web_fakes import ScriptedTutor

from llm_tutor.student import survey
from llm_tutor.web import markdown
from llm_tutor.web.api_lesson import code_message

COURSE = "mlcourse-topic01"
BASE = f"/api/courses/{COURSE}/lesson"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю.")


def _answers() -> list[int]:
    progress, given = survey.Progress(), []
    while progress.next_key() is not None:
        progress, given = progress.answer(1), [*given, 1]
    return given


@pytest.mark.parametrize("ticks", ["```", "````", "``"])
def test_code_message_fence_survives_backticks_in_code(ticks: str) -> None:
    code = f's = """\n{ticks}\n"""\nprint(s)'
    html = markdown.render(code_message(code, passed=False, report="Ошибка в коде: x"))

    # Итог проверки — обычный абзац, а не продолжение блока кода.
    assert "<p>Результат проверки: не пройдено" in html
    assert html.count("<pre") == 1
    assert ticks in re.search(r"<pre.*?</pre>", html, re.DOTALL).group(0)


async def test_new_student_is_welcomed_not_welcomed_back(student) -> None:
    html = (await student.get("/")).text

    assert "Добро пожаловать" in html
    assert "С возвращением" not in html
    assert "где вы остановились" not in html


async def test_survey_pending_home_invites_to_survey(student) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    html = (await student.get("/")).text

    assert "Начнём с короткой анкеты" in html


async def test_home_stats_do_not_repeat_course_card(student) -> None:
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    await student.post(f"{BASE}/survey/finish", json={"answers": _answers()})
    html = (await student.get("/")).text

    assert (
        html.count("Закрыто тем") == 2
    )  # подпись прогресса и её aria-label — в карточке курса
    assert "Тем впереди" in html
    assert "С возвращением" in html
