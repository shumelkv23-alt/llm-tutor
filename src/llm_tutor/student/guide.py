"""Ведение занятия: фазы, критерий закрытия узла, реакция на «застрял» (Срез 10).

Ведёт бот: он решает, что узел пройден, и объявляет следующий шаг. Но решает
это КОД по формальному критерию (архитектура, §6.1) — «понятно?» ответом не
считается, доказательство это решённые задачи.
"""

from llm_tutor.config import Settings, get_settings
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, hints
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY


def register_answer(
    state: SessionState,
    *,
    correct: bool,
    hints_used: int,
    settings: Settings | None = None,
) -> SessionState:
    """Обновляет счётчик чистых успехов и фазу после ответа на задание.

    Чистый успех — верный ответ без единой подсказки: только он идёт в зачёт
    критерия закрытия. Любой другой ответ обнуляет серию. Что ответ не повтор
    одного и того же вопроса, следит выдача заданий: отвеченное недавно она
    не предлагает (пауза ``item_repeat_cooldown_days``).
    """
    clean_success = correct and hints_used == 0
    return state.model_copy(
        update={
            "node_streak": state.node_streak + 1 if clean_success else 0,
            "phase": "check",
        }
    )


def is_node_closed(
    state: SessionState,
    mastery: beta.Mastery,
    *,
    settings: Settings | None = None,
) -> bool:
    """Пройден ли узел: серия чистых ответов ИЛИ уверенное владение (§6.1)."""
    s = settings or get_settings()
    by_streak = state.node_streak >= s.guide_success_streak
    by_mastery = (
        mastery.mean >= s.mastery_skip_threshold
        and mastery.uncertainty <= CONFIDENT_UNCERTAINTY
    )
    return by_streak or by_mastery


def on_student_stuck(state: SessionState) -> SessionState:
    """Ученик просит глубины: узел в усиленный проход, вперёд не идём.

    Счётчик успехов обнуляется — узел сейчас не закроется, значит и следующий
    не начнётся. Уровень подсказки поднимаем на ступень, не сразу на разбор.
    """
    return state.model_copy(
        update={
            "mode": "reinforce",
            "node_streak": 0,
            "hint_level": hints.next_hint_level(state.hint_level, state.hint_level + 1),
            "phase": "explain",
        }
    )
