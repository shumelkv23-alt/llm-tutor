"""Урок за ручку (срез 28): части объяснения и «ответ или реплика».

Чистые функции без БД и модели: проводка — в ``core/turn.py``.
"""

import re
from typing import Literal

from llm_tutor.grader import autocheck
from llm_tutor.schemas import Item, SessionState

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


# --- части урока (срез 28) ---

# Тема объясняется частями; разделитель — отдельная строка «---» в ответе тьютора.
MAX_PARTS = 3
_SEPARATOR = re.compile(r"^\s*---+\s*$", re.MULTILINE)

LESSON_KICKOFF = (
    "Объясни тему «{name}» по шагам: идея → пример кода → где это применяют. "
    "Каждый шаг — 2–4 предложения, пример — короткий код. Раздели шаги "
    "отдельной строкой ---. Вопросов в конце не задавай: проверку даст бот."
)
NEXT_KICKOFF_TEXT = "Дальше."
NEXT_LABEL = "Дальше ▶️"
CHECK_LABEL = "Проверим ✅"
CALLBACK_PREFIX = "lesson:next"
STALE_PART_REPLY = "Эта часть уже позади — продолжаем с последнего сообщения."


def split_parts(text: str) -> list[str]:
    """Части урока: режет по строке ``---``, пустые отбрасывает, лишние склеивает.

    Модель не всегда ставит разделитель — тогда урок из одной части, это не сбой.
    """
    parts = [part.strip() for part in _SEPARATOR.split(text) if part.strip()]
    if len(parts) > MAX_PARTS:
        parts = [*parts[: MAX_PARTS - 1], "\n\n".join(parts[MAX_PARTS - 1 :])]
    return parts or [text.strip()]


def button(state: SessionState) -> tuple[str, str] | None:
    """Кнопка урока: «Дальше» к следующей части или «Проверим» после последней.

    В колбэк зашиты тема и число показанных частей: старая или повторная
    кнопка не совпадёт с состоянием и ничего не сдвинет.
    """
    if state.current_node_id is None or state.pending_item_id is not None:
        return None
    label = NEXT_LABEL if state.lesson_part < len(state.lesson_parts) else CHECK_LABEL
    return label, f"{CALLBACK_PREFIX}:{state.current_node_id}:{state.lesson_part}"
