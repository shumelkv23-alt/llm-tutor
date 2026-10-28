"""Проект по курсу: шаблоны этапов и правила без БД и модели (срезы 97–98).

Шаблон задаёт поля карточки проекта и этапы реализации с целью, чек-листом и
полями заметок. Что заполнено, можно ли идти дальше и что из ответа модели
можно показать ученику, решает этот модуль, а не модель.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from markdown_it import MarkdownIt

# Обязательный текст карточки и заметок — не короче (после обрезки пробелов).
MIN_TEXT = 10
TITLE_MIN, TITLE_MAX = 3, 80
CARD_MAX = 1000
NOTE_MAX = 4000
CODE_MAX = 8000


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    required: bool
    max_chars: int


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    goal: str
    checklist: tuple[str, ...]
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class ProjectTemplate:
    id: str
    title: str
    card: tuple[Field, ...]
    stages: tuple[Stage, ...]


def _note(key: str, label: str, required: bool = False) -> Field:
    return Field(key, label, required, CODE_MAX if key == "code" else NOTE_MAX)


_CODE = _note("code", "Фрагмент кода или ошибка — обсудить с наставником")

ML = ProjectTemplate(
    id="ml",
    title="ML-проект",
    card=(
        Field("title", "Название проекта", True, TITLE_MAX),
        Field("problem", "Задача: что предсказываем или исследуем и зачем", True, CARD_MAX),
        Field("data", "Данные: откуда и что в них", True, CARD_MAX),
        Field("success", "Как поймём, что получилось: метрика и с чем сравниваем", True, CARD_MAX),
    ),
    stages=(
        Stage(
            "data",
            "Данные и анализ",
            "Понять данные до модели.",
            (
                "Загрузить данные",
                "Размер и типы признаков",
                "Пропуски",
                "Целевой признак и его распределение",
                "2–3 наблюдения о данных",
            ),
            (
                _note("done", "Что сделал", True),
                _note("findings", "Что увидел в данных", True),
                _note("numbers", "Цифры: размер выборки, доли классов и т. п."),
                _CODE,
            ),
        ),
        Stage(
            "baseline",
            "Базовая модель",
            "Получить точку отсчёта.",
            (
                "Отложить тестовую выборку",
                "Простая модель: константа, дерево или логистическая регрессия",
                "Метрика на отложенной выборке",
            ),
            (
                _note("model", "Какая модель и почему", True),
                _note("metric", "Метрика на отложенной выборке", True),
                _CODE,
            ),
        ),
        Stage(
            "improve",
            "Улучшения",
            "Превзойти базовую модель честно.",
            (
                "1–3 попытки: признаки, параметры, другая модель",
                "Кросс-валидация",
                "Сравнение с базовой моделью",
            ),
            (
                _note("tried", "Что пробовал", True),
                _note("result", "Что дало лучший результат, цифры", True),
                _CODE,
            ),
        ),
        Stage(
            "conclusions",
            "Выводы",
            "Ответить на задачу проекта.",
            ("Ответ на вопрос из карточки", "Ограничения", "Что дальше"),
            (
                _note("answer", "Ответ на задачу", True),
                _note("limits", "Ограничения и риски", True),
                _note("next", "Что сделал бы дальше"),
            ),
        ),
    ),
)

TEMPLATES: dict[str, ProjectTemplate] = {ML.id: ML}


# --- Проверки (срез 98) ---


def _enough(value: object, minimum: int) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def card_missing(template: ProjectTemplate, card: Mapping[str, str]) -> list[str]:
    """Поля карточки, которые не позволяют утвердить идею (порядок шаблона)."""
    missing = []
    for field in template.card:
        minimum = TITLE_MIN if field.key == "title" else MIN_TEXT
        if not _enough(card.get(field.key), minimum):
            missing.append(field.key)
    return missing


def notes_missing(stage: Stage, notes: Mapping[str, str]) -> list[str]:
    """Обязательные заметки этапа, которые ещё не заполнены."""
    return [
        field.key
        for field in stage.fields
        if field.required and not _enough(notes.get(field.key), MIN_TEXT)
    ]


def clean_fields(fields: Sequence[Field], values: Mapping[str, str]) -> dict[str, str]:
    """Значения формы по полям шаблона; чужой ключ или перебор длины — ``ValueError``."""
    known = {field.key: field for field in fields}
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        field = known.get(key)
        if field is None:
            raise ValueError(f"Неизвестное поле {key!r}")
        if not isinstance(value, str):
            raise ValueError(f"Поле {key!r} должно быть строкой")
        value = value.strip()
        if len(value) > field.max_chars:
            raise ValueError(f"Поле {key!r} длиннее {field.max_chars} знаков")
        cleaned[key] = value
    return cleaned


# Поля этапа, которых нет в итоге от наставника: фрагмент кода ученик вставляет сам.
STAGE_PROPOSAL_EXCLUDED = frozenset({"code"})
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {number.replace(",", ".") for number in _NUMBER.findall(text)}


def clean_proposal(
    template: ProjectTemplate,
    proposal: Mapping | None,
    *,
    stage: Stage | None = None,
    student_text: str | None = None,
) -> dict[str, str]:
    """Предложение модели: строки нужных полей, обрезанные по длине; пустое — выброшено.

    Без ``stage`` — поля карточки; со ``stage`` — заметки этого этапа, кроме
    ``STAGE_PROPOSAL_EXCLUDED`` (срез 102). ``student_text`` включает страж чисел:
    итог этапа — пересказ слов ученика, и поле с числом, которого ученик не
    называл, выбрасывается целиком — модель не «досочинит» метрику.
    """
    if not isinstance(proposal, Mapping):
        return {}
    if stage is None:
        fields: Sequence[Field] = template.card
    else:
        fields = [field for field in stage.fields if field.key not in STAGE_PROPOSAL_EXCLUDED]
    said = _numbers(student_text) if student_text is not None else None
    cleaned: dict[str, str] = {}
    for field in fields:
        value = proposal.get(field.key)
        if not isinstance(value, str) or not (value := value.strip()[: field.max_chars].strip()):
            continue
        if said is not None and not _numbers(value) <= said:
            continue
        cleaned[field.key] = value
    return cleaned


# Весь ответ может нести кода не больше, чем в TOTAL_CODE_FACTOR блоках предельной длины:
# иначе решение «по шагам» — пять блоков по 8 строк — проходило бы проверку.
TOTAL_CODE_FACTOR = 2
_MARKDOWN = MarkdownIt("commonmark")


def code_lines(text: str) -> tuple[int, int]:
    """``(самый длинный блок, всего строк)`` кода в ответе, как их нарисует markdown.

    Кодом считаются все блоки commonmark: ограждения из обратных кавычек и ``~~~``
    (незакрытое тянется до конца) и блоки с отступом в четыре пробела. Тройные
    кавычки внутри строки блоком не считаются. Пустые строки не в счёт.
    """
    longest = total = 0
    for token in _MARKDOWN.parse(text):
        if token.type in ("fence", "code_block"):
            lines = sum(1 for line in token.content.split("\n") if line.strip())
            longest, total = max(longest, lines), total + lines
    return longest, total


def reply_ok(text: str, *, max_code_lines: int, max_chars: int) -> bool:
    """Реплику наставника можно показать: не пустая, не длинная, без решения кодом.

    Проверка эвристическая: код в прозе без блоков она не ловит.
    """
    if not text.strip() or len(text) > max_chars:
        return False
    longest, total = code_lines(text)
    return longest <= max_code_lines and total <= max_code_lines * TOTAL_CODE_FACTOR


def stage_of(template: ProjectTemplate, stage_id: str) -> Stage:
    for stage in template.stages:
        if stage.id == stage_id:
            return stage
    raise KeyError(stage_id)


def next_stage(template: ProjectTemplate, stage_id: str) -> str | None:
    ids = [stage.id for stage in template.stages]
    index = ids.index(stage_id)
    return ids[index + 1] if index + 1 < len(ids) else None
