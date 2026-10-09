"""Урок за ручку (срез 28): части объяснения и «ответ или реплика».

Чистые функции без БД и модели: проводка — в ``core/turn.py``.
"""

from typing import Literal

from llm_tutor.grader import autocheck
from llm_tutor.schemas import Item

AnswerKind = Literal["answer", "chat"]

# Короткий ответ — имя параметра, число, слово; длиннее — уже фраза тьютору.
SHORT_ANSWER_MAX_WORDS = 3
_QUESTION_STARTS = (
    "что", "как", "почему", "зачем", "а ", "какой", "какая", "какие", "где",
    "когда", "объясни", "не понял", "подожди",
)


def answer_kind(item: Item, text: str) -> AnswerKind:
    """Ответ на висящее задание или реплика тьютору (посторонний вопрос).

    Реплику нельзя записывать в журнал неверным ответом: ученик спрашивал, а
    не отвечал. Для вопроса с вариантами ответ — только номер или текст
    варианта; для короткого — короткая реплика без признаков вопроса.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if item.answer_type == "choice":
        options = {autocheck.normalize_answer(option) for option in item.options}
        is_number = stripped.isdigit() and 1 <= int(stripped) <= len(item.options)
        is_option = autocheck.normalize_answer(stripped) in options
        return "answer" if is_number or is_option else "chat"
    if item.answer_type == "short":
        looks_question = stripped.endswith("?") or lowered.startswith(_QUESTION_STARTS)
        is_short = len(stripped.split()) <= SHORT_ANSWER_MAX_WORDS
        return "answer" if is_short and not looks_question else "chat"
    # Открытое и код-задание — развёрнутый ответ: вопросом считаем только «?».
    return "chat" if stripped.endswith("?") else "answer"
