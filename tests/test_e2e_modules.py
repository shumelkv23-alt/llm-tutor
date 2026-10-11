"""Сквозной сценарий ядра: последняя тема модуля 1 → «🎉» → вход в модуль 2 (срез 27).

Бот удалён; вторая половина сценария (анкета модуля 2 в браузере → урок)
проверяется тестами веба (срез 52).
"""

from course_fixtures import T1, correct_answer, finish_all_but, load_two_modules
from web_fakes import GradingTutor

from llm_tutor.core.turn import handle_turn
from llm_tutor.db import repos
from llm_tutor.student import survey


async def test_closing_module_one_announces_it_and_asks_for_module_two_survey(
    conn, settings
) -> None:
    load_two_modules(conn)
    survey.apply_answers(
        conn, T1, {b.key: 1 for b in T1.blocks}, level=survey.LEVEL_SOME, settings=settings
    )
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)
    client = GradingTutor(conn, passed=True)

    reply = await handle_turn(
        conn, client, "m", correct_answer(item), now=10.0, settings=settings,
        allow_intents=False, answer_mode="answer",
    )

    assert "🎉 Модуль 1" in reply.text
    lesson = repos.get_session_state(conn, repos.get_open_session(conn))
    # Анкеты модуля 2 ещё нет: тема не выбрана, урок продолжится после анкеты.
    assert lesson.current_node_id is None and lesson.pending_item_id is None
