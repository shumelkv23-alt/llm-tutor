"""Вид сообщения анкеты: вопросы, сетка вариантов, «Назад», сводка."""

from llm_tutor.bot import start
from llm_tutor.bot import survey as survey_bot
from llm_tutor.student import survey
from llm_tutor.student.survey import Progress


def _labels(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_intro_view_has_single_go_button() -> None:
    text, markup = survey_bot.intro_view()

    assert text == start.INTRO_TEXT
    assert _labels(markup) == [[survey_bot.GO_LABEL]]
    assert markup.inline_keyboard[0][0].callback_data == survey_bot.GO_DATA


def test_level_question_has_one_option_per_row_and_no_back() -> None:
    text, markup = survey_bot.question_view(Progress())

    assert survey.LEVEL_QUESTION in text
    assert _labels(markup) == [[label] for label in survey.LEVEL_OPTIONS]


def test_block_question_is_two_by_two_grid_with_back() -> None:
    _, markup = survey_bot.question_view(Progress().answer(survey.LEVEL_SOME))

    assert _labels(markup) == [
        list(survey.SELF_LEVELS[:2]),
        list(survey.SELF_LEVELS[2:]),
        [survey_bot.BACK_LABEL],
    ]


def test_block_question_shows_progress_question_and_example() -> None:
    text, _ = survey_bot.question_view(Progress().answer(survey.LEVEL_SOME))

    first = survey.BLOCKS[0]
    assert "Вопрос 1 из 5" in text
    assert "▰▱▱▱▱" in text
    assert f"<b>{first.question}</b>" in text
    assert first.example in text


def test_callback_data_carries_step() -> None:
    progress = Progress().answer(survey.LEVEL_SOME).answer(2)
    _, markup = survey_bot.question_view(progress)

    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert all(item.startswith(f"survey:{progress.step}:") for item in data)
    assert data[-1] == f"survey:{progress.step}:{survey_bot.BACK}"


def test_summary_lists_every_block() -> None:
    answers = Progress().answer(survey.LEVEL_FROM_SCRATCH).final_answers()

    text = survey_bot.summary_text(answers)

    assert text.startswith("✅ Понял тебя")
    assert all(f"• {block.title} — впервые вижу" in text for block in survey.BLOCKS)
