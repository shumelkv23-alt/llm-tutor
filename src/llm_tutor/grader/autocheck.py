"""Детерминированная проверка заданий choice/short — без LLM (Срез 4.6).

Самый надёжный источник свидетельств в MVP (уровни 1–2 из архитектуры):
ответ сверяет код, а не модель, поэтому и вердикт, и уверенность точные.
``open``/``code`` сюда не попадают — их оценивает рубричный грейдер (Срез 6).
"""

import math
import re

from llm_tutor.schemas import CriterionResult, GradeResult, Item

# Допуск сравнения чисел — только на разницу представления («0.3» и
# «0.30000000000000004»). Не «примерная близость»: 1000 и 1001 — разные ответы.
_NUMBER_REL_TOLERANCE = 1e-9

_WHITESPACE_RE = re.compile(r"\s+")
_SURROUNDING_RE = re.compile(r"^[\s\"'«»`]+|[\s\"'«»`]+$")


class AutoCheckError(ValueError):
    """Задание не подходит для автопроверки (чужой тип или нет эталона)."""


def normalize_answer(text: str) -> str:
    """Приводит ответ к сравнимому виду.

    Регистр, ``ё``→``е``, кратные пробелы, обрамляющие кавычки/бэктики и
    хвостовые ``()`` (метод пишут и как ``groupby``, и как ``groupby()``).
    """
    stripped = _SURROUNDING_RE.sub("", text.strip())
    lowered = _WHITESPACE_RE.sub(" ", stripped.lower().replace("ё", "е"))
    if lowered.endswith("()"):
        lowered = lowered[:-2].rstrip()
    return lowered


def _as_number(text: str) -> float | None:
    """Число из ответа (запятая как десятичный разделитель тоже принимается)."""
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _option_text(item: Item, raw: str) -> str | None:
    """Разворачивает ответ на choice (индекс или текст) в текст варианта."""
    candidate = normalize_answer(raw)
    # Сначала текст варианта: у варианта-числа («150») цифры — это ответ, а не
    # индекс, иначе верная кнопка засчитывалась бы ошибкой (финальное ревью, F-1).
    if candidate in (normalize_answer(option) for option in item.options):
        return candidate
    if candidate.isdigit():
        index = int(candidate)
        if 0 <= index < len(item.options):
            return normalize_answer(item.options[index])
        return None
    return candidate or None


def _check_choice(item: Item, answer: str) -> bool:
    # Эталон — всегда индекс варианта, даже если варианты сами числа.
    raw = (item.answer or "").strip()
    index = int(raw) if raw.isdigit() else -1
    expected = normalize_answer(item.options[index]) if 0 <= index < len(item.options) else None
    if expected is None:
        # Битый эталон (индекс вне вариантов) — падаем, а не пишем «неверно»
        # за верный ответ: молчаливый ноль отравил бы модель ученика.
        raise AutoCheckError(f"У задания {item.id} эталон вне вариантов: {item.answer!r}")
    return _option_text(item, answer) == expected


def _check_short(item: Item, answer: str) -> bool:
    expected = normalize_answer(item.answer or "")
    if not expected:
        raise AutoCheckError(f"У задания {item.id} пустой эталон")
    given = normalize_answer(answer)
    if not given:
        return False
    if given == expected:
        return True
    expected_number, given_number = _as_number(expected), _as_number(given)
    if expected_number is None or given_number is None:
        return False
    return math.isclose(
        expected_number, given_number, rel_tol=_NUMBER_REL_TOLERANCE, abs_tol=0.0
    )


def check(item: Item, answer: str) -> GradeResult:
    """Проверяет ответ на задание ``choice``/``short`` и возвращает вердикт.

    Для прочих типов заданий — ``AutoCheckError``: их маршрут другой (грейдер).
    """
    if item.answer is None:
        raise AutoCheckError(f"У задания {item.id} нет эталона для проверки")

    if item.answer_type == "choice":
        passed = _check_choice(item, answer)
    elif item.answer_type == "short":
        passed = _check_short(item, answer)
    else:
        raise AutoCheckError(
            f"Автопроверка не умеет answer_type={item.answer_type!r} (задание {item.id})"
        )

    return GradeResult(
        criteria=[CriterionResult(criterion="точный ответ", passed=passed, quote=answer)],
        score=1.0 if passed else 0.0,
        confidence=1.0,  # проверка детерминированная — уверенность максимальная
    )
