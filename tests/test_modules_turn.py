"""Переход между модулями в ходе урока (срез 25)."""

from course_fixtures import correct_answer, finish_all_but, load_two_modules
from fakes import GradingTutor

from llm_tutor.core.turn import COURSE_DONE_NOTE, handle_turn
from llm_tutor.db import repos
from llm_tutor.schemas import Route


def _state(conn):
    return repos.get_session_state(conn, repos.get_open_session(conn))


def _module_two_survey_done(conn) -> None:
    for key in ("t02_level", "t02_basics", "t02_relations"):
        repos.set_fact(conn, key, "Знаю в теории", source="self")


async def test_closing_last_topic_of_module_moves_to_next(conn, settings) -> None:
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "🎉 Модуль 1" in reply.text
    assert "Дальше — модуль 2" in reply.text
    state = _state(conn)
    assert state.route.completed_topics == [1]
    assert state.route.topic_id == 2
    assert state.current_node_id == "mini_plots"
    # Срез 28: урок модуля 2 начался частями, задание — после «Проверим».
    assert state.lesson_parts and state.pending_item_id is None


async def test_returning_to_completed_module_has_no_fanfare(conn, settings) -> None:
    """Возврат к теме пройденного модуля: без повторного «🎉», дальше — модуль 2."""
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1, completed=(1,))

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "🎉" not in reply.text
    assert _state(conn).route.topic_id == 2


async def test_last_topic_of_course_says_course_done_once(conn, settings) -> None:
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "mini_corr", topic_id=2, completed=(1,))

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "🎉 Модуль 2" in reply.text
    assert reply.text.count("курс пройден") == 1
    assert COURSE_DONE_NOTE in reply.text
    assert reply.tail is None
    state = _state(conn)
    assert state.current_node_id is None and state.pending_item_id is None
    assert state.route.topic_id is None


async def test_legacy_snapshot_does_not_end_course_early(conn, settings) -> None:
    """Снимок до среза 25 (только темы topic01): конец модуля 1 — не конец курса."""
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    legacy = Route(
        steps=[
            s.model_copy(update={"closed_at": None})
            for s in state.route.steps
            if not s.concept_id.startswith("mini_")
        ]
    )
    repos.update_session_state(conn, session_id, state.model_copy(update={"route": legacy}))

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "курс пройден" not in reply.text
    assert "Дальше — модуль 2" in reply.text


async def test_next_module_without_survey_waits_for_it(conn, settings) -> None:
    """Модуль 1 пройден, анкета модуля 2 не пройдена — задания нет, ждём анкету."""
    load_two_modules(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "Дальше — модуль 2" in reply.text
    assert "пара вопросов" in reply.text
    state = _state(conn)
    assert state.current_node_id is None and state.pending_item_id is None
    assert state.route.topic_id == 2


async def test_reclosing_topic_after_course_end_has_no_second_graduation(conn, settings) -> None:
    """25-L2: курс уже пройден, ученик повторил закрытую тему — «🎓» второй раз нет."""
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "mini_corr", topic_id=2, completed=(1, 2))
    session_id = repos.get_open_session(conn)
    state = _state(conn)
    steps = [
        step.model_copy(update={"status": "closed", "closed_at": 0.5}) for step in state.route.steps
    ]
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"route": state.route.model_copy(update={"steps": steps})})
    )

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert COURSE_DONE_NOTE not in reply.text
    assert "🎉" not in reply.text
    assert "/plan" in reply.text
