"""Сквозной сценарий: модуль 1 пройден → анкета модуля 2 → урок модуля 2 (срез 27)."""

from course_fixtures import T1, T2, correct_answer, finish_all_but, load_two_modules
from fakes import FakeCallback, FakeMessage, GradingTutor, _fsm, _named

from llm_tutor.bot.handlers import make_router
from llm_tutor.bot.survey import GO_LABEL, make_survey_router
from llm_tutor.db import repos
from llm_tutor.student import survey


def _data_of(message: FakeMessage, label: str) -> str:
    for row in message.reply_markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return button.callback_data
    raise AssertionError(f"нет кнопки {label!r}")


async def test_student_moves_from_module_one_to_module_two(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(
        conn, T1, {b.key: 1 for b in T1.blocks}, level=survey.LEVEL_SOME, settings=settings
    )
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)
    client = GradingTutor(conn, passed=True)
    router = make_router(conn, client, "m", settings=settings)
    survey_router = make_survey_router(conn, settings, client, "m")
    click = _named(survey_router, "callback_query", "on_survey_click")
    state = _fsm()
    chat = FakeMessage(correct_answer(item))

    await _named(router, "message", "on_text")(chat, state)

    texts = [text for text, _ in chat.sent]
    assert any("🎉 Модуль 1" in text for text in texts)
    intro = FakeMessage(texts[-1])
    intro.message_id = (await state.get_data())["message_id"]
    intro.reply_markup = chat.sent[-1][1]

    for label in (
        GO_LABEL,
        T2.level_options[survey.LEVEL_SOME],
        survey.SELF_LEVELS[1],
        survey.SELF_LEVELS[1],
    ):
        await click(FakeCallback(_data_of(intro, label), intro), state)

    lesson = repos.get_session_state(conn, repos.get_open_session(conn))
    assert lesson.current_node_id == "mini_plots"
    assert lesson.pending_item_id is not None and lesson.pending_item_id >= 2000
    assert any(text.startswith("📋") for text, _ in intro.sent)
    assert survey.is_completed(conn, T2)
