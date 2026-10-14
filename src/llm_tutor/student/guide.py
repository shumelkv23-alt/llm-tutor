"""Ведение занятия: фазы, критерий закрытия узла, реакция на «застрял» (Срез 10).

Ведёт бот: он решает, что узел пройден, и объявляет следующий шаг. Но решает
это КОД по формальному критерию (архитектура, §6.1) — «понятно?» ответом не
считается, доказательство это решённые задачи.
"""

from collections.abc import Sequence
from typing import Literal

from llm_tutor.config import Settings, get_settings
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta, hints
from llm_tutor.student.planner import CONFIDENT_UNCERTAINTY


def register_answer(
    state: SessionState,
    *,
    correct: bool,
    hinted: bool,
    settings: Settings | None = None,
) -> SessionState:
    """Обновляет счётчик чистых успехов и фазу после ответа на задание.

    Чистый успех — верный ответ, полученный БЕЗ помощи: только он идёт в зачёт
    критерия закрытия. ``hinted`` — это факт («ученик спросил или попросил
    глубины, пока задание висело»), а не выбранный моделью уровень подсказки:
    иначе достаточно было бы спросить объяснение и ответить.

    Требование чистоты относится к проверочному проходу: там ученик сам
    утверждает, что тему знает, и помощь обесценивает доказательство. В уроке
    бот объясняет первым по своей же схеме (§5.2), поэтому вызывающий код
    передаёт ``hinted=False`` — серию считают верные ответы.

    Что ответ — не повтор только что заданного вопроса, следит выдача заданий:
    внутри захода задание не выдаётся дважды (``verification_item``).
    """
    clean_success = correct and not hinted
    return state.model_copy(
        update={
            "node_streak": state.node_streak + 1 if clean_success else 0,
            "phase": "check",
        }
    )


def on_verification_failed(state: SessionState) -> SessionState:
    """Проход не подтвердился: узел в усиленный проход, проход снят.

    Ученик заявил, что знает тему, но не подтвердил — значит тему надо
    разобрать, а не продолжать допрашивать.
    """
    return state.model_copy(
        update={
            "mode": "reinforce",
            "node_streak": 0,
            "verify_item_ids": [],
            "phase": "explain",
        }
    )


def streak_target(state: SessionState, settings: Settings, sections: int = 0) -> int:
    """Сколько верных ответов подряд закрывают узел в этом заходе.

    Проход «закрой тему» длиннее урока: ученик сам просит засчитать тему.
    Проход по занятию с разделами (срез 54) — по вопросу на каждый раздел и
    итоговое задание, кто бы его ни начал.
    """
    if state.mode == "verify" and sections:
        return sections + 1
    if state.mode == "verify" and state.close_requested:
        return settings.close_success_streak
    return settings.guide_success_streak


def is_node_closed(
    state: SessionState,
    mastery: beta.Mastery,
    *,
    settings: Settings | None = None,
    sections: Sequence[str] = (),
    final_required: bool = True,
) -> bool:
    """Пройден ли узел (§6.1) — решает код, а не модель.

    Тема без разделов: серия чистых ответов ИЛИ уверенное владение. Занятие с
    разделами (``sections`` — их id, срез 54): пройдены все разделы и
    итоговое задание (слабые тоже считаются пройденными) ИЛИ уверенное
    владение; в проверочном проходе — серия по вопросу на раздел и итоговое.

    ``final_required=False`` — итогового задания в банке ещё нет (урок
    пишется): требовать нечего, занятие закрывают разделы.
    """
    s = settings or get_settings()
    by_mastery = (
        mastery.mean >= s.mastery_skip_threshold
        and mastery.uncertainty <= CONFIDENT_UNCERTAINTY
    )
    if sections and state.mode != "verify":
        return by_mastery or (
            lesson_sections_done(state, sections)
            and (state.final_passed or not final_required)
        )
    by_streak = state.node_streak >= streak_target(state, s, len(sections))
    return by_streak or by_mastery


# --- занятие с разделами (срез 54) ---

# Поля прогресса по разделам: вход в другое занятие обнуляет их все.
SECTION_RESET: dict = {
    "section_id": None,
    "passed_sections": [],
    "weak_sections": [],
    "skipped_sections": [],
    "final_passed": False,
    "final_weak": False,
}

SectionOutcome = Literal["passed", "weak", "retry"]


def next_section(
    sections: Sequence[str],
    passed: Sequence[str],
    skipped: Sequence[str] = (),
) -> str | None:
    """Первый непройденный раздел занятия; ``None`` — очередь итогового задания.

    Непроверяемые разделы (``skipped``) очередь не держат: у них нет заданий.
    """
    done = set(passed) | set(skipped)
    return next((section for section in sections if section not in done), None)


def lesson_sections_done(state: SessionState, sections: Sequence[str]) -> bool:
    """Все разделы занятия пройдены (слабые и непроверяемые — тоже)."""
    done = set(state.passed_sections) | set(state.skipped_sections)
    return all(section in done for section in sections)


def current_section(state: SessionState, sections: Sequence[str]) -> str | None:
    """Раздел, по которому сейчас идёт урок; ``None`` — итоговое задание.

    Записанный в состоянии раздел берётся, только если он ещё не пройден:
    иначе (состояние потеряно, раздел убрали из seed) — первый непройденный.
    """
    if (
        state.section_id in sections
        and state.section_id not in state.passed_sections
        and state.section_id not in state.skipped_sections
    ):
        return state.section_id
    return next_section(sections, state.passed_sections, state.skipped_sections)


def on_section_answer(
    state: SessionState, *, correct: bool, sections: Sequence[str]
) -> tuple[SessionState, SectionOutcome]:
    """Ответ на вопрос раздела или итоговое задание в уроке (план §1.6).

    Верно — раздел пройден. Ошибка — второй шанс (``retry``). Вторая ошибка —
    раздел пройден со слабым местом (⚠️), урок идёт дальше. Итоговое задание —
    по тому же правилу. После прохождения ``section_id`` указывает на
    следующий раздел (или ``None`` — очередь итогового).
    """
    section = current_section(state, sections)
    if not correct and state.check_misses < 1:
        return state.model_copy(update={"check_misses": 1, "section_id": section}), "retry"
    weak = not correct
    if section is None:
        update: dict = {"final_passed": True, "final_weak": state.final_weak or weak}
    else:
        update = {
            "passed_sections": [*state.passed_sections, section],
            "weak_sections": (
                [*state.weak_sections, section] if weak else state.weak_sections
            ),
        }
    passed = update.get("passed_sections", state.passed_sections)
    update.update(
        {
            "check_misses": 0,
            "section_id": next_section(sections, passed, state.skipped_sections),
            # Новый раздел — новый урок частями.
            "lesson_parts": [],
            "lesson_part": 0,
        }
    )
    return state.model_copy(update=update), "weak" if weak else "passed"


def lesson_weak(state: SessionState) -> bool:
    """Занятие закрывается со слабым местом: слабый раздел или итоговое."""
    return bool(state.weak_sections) or state.final_weak


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
            # Помощь по узлу чистым свидетельством не является: проход обрывается.
            "verify_item_ids": [],
        }
    )
