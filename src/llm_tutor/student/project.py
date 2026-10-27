"""Проект по курсу: шаблоны этапов и правила без БД и модели (срезы 97–98).

Шаблон задаёт поля карточки проекта и этапы реализации с целью, чек-листом и
полями заметок. Что заполнено, можно ли идти дальше и что из ответа модели
можно показать ученику, решает этот модуль, а не модель.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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


def clean_proposal(template: ProjectTemplate, proposal: Mapping | None) -> dict[str, str]:
    """Предложение модели: только поля карточки, строки, обрезанные по длине; пустое — выброшено."""
    if not isinstance(proposal, Mapping):
        return {}
    cleaned: dict[str, str] = {}
    for field in template.card:
        value = proposal.get(field.key)
        if isinstance(value, str) and (value := value.strip()[: field.max_chars].strip()):
            cleaned[field.key] = value
    return cleaned


_CODE_BLOCK = re.compile(r"```[^\n]*\n?(.*?)(?:```|\Z)", re.DOTALL)


def reply_ok(text: str, *, max_code_lines: int, max_chars: int) -> bool:
    """Реплику наставника можно показать: не пустая, не длинная, без большого блока кода."""
    if not text.strip() or len(text) > max_chars:
        return False
    for match in _CODE_BLOCK.finditer(text):
        lines = [line for line in match.group(1).split("\n") if line.strip()]
        if len(lines) > max_code_lines:
            return False
    return True


def stage_of(template: ProjectTemplate, stage_id: str) -> Stage:
    for stage in template.stages:
        if stage.id == stage_id:
            return stage
    raise KeyError(stage_id)


def next_stage(template: ProjectTemplate, stage_id: str) -> str | None:
    ids = [stage.id for stage in template.stages]
    index = ids.index(stage_id)
    return ids[index + 1] if index + 1 < len(ids) else None
