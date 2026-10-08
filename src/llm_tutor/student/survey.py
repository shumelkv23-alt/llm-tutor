"""Анкета холодного старта: профиль в ``facts`` + слабый априор.

Модель ученика стартует с априора Beta(1,1) — «не знаем». Анкета не заменяет
диагностику, а даёт лишь слабые свидетельства ``source='self'``: их вес мал
(``self_evidence_weight``), чтобы самооценка не подменяла реальные задания.

Вопросы — по блокам узлов курса (срез 19): вместо общих «опыт / цель / время»
пять тематических, каждый про свой участок темы. Цель маршрута не спрашиваем:
маршрут строим по всей теме целиком.
"""

import sqlite3
import time
from dataclasses import dataclass, field

from llm_tutor.config import Settings, get_settings
from llm_tutor.db import repos
from llm_tutor.student import self_report

# Ключ факта цели: анкетой больше не заполняется, но маршрут его читает.
GOAL_CONCEPT_KEY = "goal_concept_id"

# Градации самооценки — общие для всех вопросов.
SELF_LEVELS: tuple[str, ...] = (
    "Не знаю",
    "Слышал(а), но не делал(а)",
    "Делал(а), но с подсказками",
    "Уверенно",
)

# Во что превращается индекс ответа: 0 — «не знаю», 3 — «уверенно».
PRIOR_LEVELS: tuple[float, ...] = (0.0, 0.3, 0.65, 1.0)

# Блоки темы: ключ вопроса, название для текста, узлы блока.
BLOCKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("python", "Основы Python и NumPy", ("python_basics", "numpy_basics")),
    (
        "tables",
        "Таблицы pandas: Series и DataFrame",
        ("pandas_intro", "pandas_series", "pandas_dataframe"),
    ),
    (
        "loading",
        "Чтение и осмотр данных",
        ("read_csv", "df_inspect", "describe_stats", "dtype_conversion"),
    ),
    (
        "selection",
        "Выборка и упорядочивание",
        (
            "indexing_loc_iloc",
            "boolean_indexing",
            "sorting",
            "value_counts",
            "df_transformations",
        ),
    ),
    (
        "analysis",
        "Группировки и разведочный анализ",
        (
            "apply_functions",
            "groupby",
            "agg_functions",
            "summary_tables",
            "visualization_basics",
            "eda_workflow",
            "churn_eda_case",
        ),
    ),
)


@dataclass(frozen=True)
class SurveyOption:
    """Вариант ответа анкеты."""

    label: str


@dataclass(frozen=True)
class SurveyQuestion:
    """Один вопрос анкеты с вариантами (в боте — инлайн-кнопки)."""

    key: str
    text: str
    options: list[SurveyOption] = field(default_factory=list)


SURVEY_QUESTIONS: tuple[SurveyQuestion, ...] = tuple(
    SurveyQuestion(
        key=f"block_{block_key}",
        text=f"Как у тебя с блоком «{title}»?",
        options=[SurveyOption(label) for label in SELF_LEVELS],
    )
    for block_key, title, _ in BLOCKS
)


def _block_concepts(question_key: str) -> tuple[str, ...]:
    """Узлы блока по ключу вопроса (``KeyError``, если такого блока нет)."""
    for block_key, _, concepts in BLOCKS:
        if question_key == f"block_{block_key}":
            return concepts
    raise KeyError(f"Нет блока анкеты с ключом {question_key!r}")


def question_by_key(key: str) -> SurveyQuestion:
    """Вопрос анкеты по ключу факта (``KeyError``, если такого нет)."""
    for question in SURVEY_QUESTIONS:
        if question.key == key:
            return question
    raise KeyError(f"Нет вопроса анкеты с ключом {key!r}")


def apply_answers(
    conn: sqlite3.Connection,
    answers: dict[str, int],
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> None:
    """Сохраняет ответы анкеты в ``facts`` и раздаёт слабый априор по блокам.

    ``answers`` — ключ вопроса → индекс выбранного варианта. Незаполненные
    вопросы просто не сохраняются (анкету можно прервать). Повторное
    применение факты перезаписывает, но априор **не** начисляет заново:
    иначе самооценка накрутила бы счётчики.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    for key, index in answers.items():
        question = question_by_key(key)
        if not 0 <= index < len(question.options):
            raise ValueError(
                f"Нет варианта {index} у вопроса {key!r} (их {len(question.options)})"
            )
        # Априор блока начисляем, только когда ответ на него звучит впервые:
        # иначе повторное прохождение анкеты накрутило бы самооценку.
        fresh = repos.get_fact(conn, key) is None
        repos.set_fact(conn, key, question.options[index].label, source="self")
        if fresh:
            for concept_id in _block_concepts(key):
                self_report.apply(
                    conn,
                    concept_id,
                    correct=PRIOR_LEVELS[index],
                    now=stamp,
                    settings=s,
                    commit=False,
                )
    conn.commit()


def is_completed(conn: sqlite3.Connection) -> bool:
    """Прошёл ли ученик анкету: ответы есть на все вопросы."""
    return all(
        repos.get_fact(conn, question.key) is not None for question in SURVEY_QUESTIONS
    )
