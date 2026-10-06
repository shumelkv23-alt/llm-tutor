"""Тесты ведения: фазы, критерий закрытия, «застрял» (Срез 10)."""

from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, guide


def _mastery(mean: float, uncertainty: float) -> beta.Mastery:
    return beta.Mastery("x", 1.0, 1.0, mean, uncertainty, 0.0, None)


def test_two_correct_answers_without_hints_close_node(settings) -> None:
    state = SessionState(current_node_id="groupby")

    state = guide.register_answer(state, correct=True, hinted=False, settings=settings)
    state = guide.register_answer(state, correct=True, hinted=False, settings=settings)

    assert state.node_streak == 2
    assert guide.is_node_closed(state, _mastery(0.6, 0.2), settings=settings) is True


def test_answer_with_hints_resets_streak(settings) -> None:
    """Задача, решённая с подсказкой, доказательством не считается."""
    state = SessionState(current_node_id="groupby")

    state = guide.register_answer(state, correct=True, hinted=False, settings=settings)
    state = guide.register_answer(state, correct=True, hinted=True, settings=settings)

    assert state.node_streak == 0
    assert guide.is_node_closed(state, _mastery(0.6, 0.2), settings=settings) is False


def test_wrong_answer_resets_streak(settings) -> None:
    state = SessionState(current_node_id="groupby")
    state = guide.register_answer(state, correct=True, hinted=False, settings=settings)

    state = guide.register_answer(state, correct=False, hinted=False, settings=settings)

    assert state.node_streak == 0


def test_answer_moves_to_check_phase(settings) -> None:
    state = SessionState(current_node_id="groupby", phase="practice")

    state = guide.register_answer(state, correct=True, hinted=False, settings=settings)

    assert state.phase == "check"


def test_confident_mastery_closes_node_without_tasks(settings) -> None:
    """Второй путь критерия (§6.1): узел закрывается по уверенному владению."""
    state = SessionState(current_node_id="groupby")

    assert guide.is_node_closed(state, _mastery(0.95, 0.03), settings=settings) is True


def test_uncertain_mastery_does_not_close_node(settings) -> None:
    """Высокая оценка без подтверждения узлом не считается."""
    state = SessionState(current_node_id="groupby")

    assert guide.is_node_closed(state, _mastery(0.95, 0.25), settings=settings) is False


def test_streak_length_comes_from_settings(settings) -> None:
    strict = settings.model_copy(update={"guide_success_streak": 3})
    state = SessionState(current_node_id="groupby")
    for _ in range(2):
        state = guide.register_answer(state, correct=True, hinted=False, settings=strict)

    assert guide.is_node_closed(state, _mastery(0.6, 0.2), settings=strict) is False


# --- «ученик застрял» ---


def test_stuck_switches_node_to_reinforce_and_resets_streak(settings) -> None:
    """«Не понял» не пускает вперёд: узел уходит в усиленный проход."""
    state = SessionState(current_node_id="groupby", node_streak=1, hint_level=0)

    state = guide.on_student_stuck(state)

    assert state.mode == "reinforce"
    assert state.node_streak == 0
    assert state.hint_level == 1  # ступень вверх, но не разбор
    assert state.phase == "explain"


def test_stuck_does_not_jump_the_ladder(settings) -> None:
    state = SessionState(current_node_id="groupby", hint_level=2)

    state = guide.on_student_stuck(state)

    assert state.hint_level == 3


def test_stuck_keeps_student_on_the_same_node(settings) -> None:
    state = SessionState(current_node_id="groupby")

    state = guide.on_student_stuck(state)

    assert state.current_node_id == "groupby"
