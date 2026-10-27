"""Проект по курсу: шаблоны этапов и правила без БД и модели (срезы 97–98).

Шаблон задаёт поля карточки проекта и этапы реализации с целью, чек-листом и
полями заметок. Что заполнено, можно ли идти дальше и что из ответа модели
можно показать ученику, решает этот модуль, а не модель.
"""

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
