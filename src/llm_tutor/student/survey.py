"""Анкета холодного старта: профиль в ``facts`` + слабый априор (Срез 4.7).

Модель ученика стартует с априора Beta(1,1) — «не знаем». Анкета не заменяет
диагностику, а даёт лишь слабые свидетельства ``source='self'``: их вес мал
(``self_evidence_weight``), чтобы самооценка не подменяла реальные задания.
"""

import sqlite3
import time
from dataclasses import dataclass, field

from llm_tutor.config import Settings, get_settings
from llm_tutor.db import repos
from llm_tutor.schemas import Event
from llm_tutor.student import beta

# Ключи фактов профиля.
EXPERIENCE_KEY = "pandas_experience"
GOAL_KEY = "goal"
GOAL_CONCEPT_KEY = "goal_concept_id"
TIME_BUDGET_KEY = "time_budget"


@dataclass(frozen=True)
class SurveyOption:
    """Вариант ответа: что сказать и (для цели) какой концепт он нацеливает."""

    label: str
    target_concept: str | None = None


@dataclass(frozen=True)
class SurveyQuestion:
    """Один вопрос анкеты с вариантами (в боте — инлайн-кнопки)."""

    key: str
    text: str
    options: list[SurveyOption] = field(default_factory=list)


SURVEY_QUESTIONS: tuple[SurveyQuestion, ...] = (
    SurveyQuestion(
        key=EXPERIENCE_KEY,
        text="Насколько ты знаком с Python и pandas?",
        options=[
            SurveyOption("На Python не писал"),
            SurveyOption("Python знаю, pandas — нет"),
            SurveyOption("pandas трогал(а) пару раз"),
            SurveyOption("Работаю с pandas уверенно"),
        ],
    ),
    SurveyQuestion(
        key=GOAL_KEY,
        text="Зачем тебе эта тема?",
        options=[
            SurveyOption("Разобраться с основами pandas", target_concept="pandas_dataframe"),
            SurveyOption("Освоить группировки и сводные таблицы", target_concept="summary_tables"),
            SurveyOption("Пройти тему 1 целиком", target_concept="churn_eda_case"),
        ],
    ),
    SurveyQuestion(
        key=TIME_BUDGET_KEY,
        text="Сколько времени в неделю готов заниматься?",
        options=[
            SurveyOption("Меньше часа"),
            SurveyOption("1–3 часа"),
            SurveyOption("3–7 часов"),
            SurveyOption("Больше 7 часов"),
        ],
    ),
)


# Самооценка опыта → слабый априор по фундаментальным концептам.
# Программист без pandas получает высокий python_basics и низкие pandas-узлы.
_EXPERIENCE_PRIOR: tuple[dict[str, float], ...] = (
    {"python_basics": 0.0},
    {"python_basics": 1.0, "pandas_intro": 0.0},
    {"python_basics": 1.0, "pandas_intro": 1.0, "pandas_series": 1.0, "pandas_dataframe": 1.0},
    {
        "python_basics": 1.0,
        "pandas_intro": 1.0,
        "pandas_series": 1.0,
        "pandas_dataframe": 1.0,
        "read_csv": 1.0,
        "df_inspect": 1.0,
        "indexing_loc_iloc": 1.0,
    },
)


def question_by_key(key: str) -> SurveyQuestion:
    """Вопрос анкеты по ключу факта (``KeyError``, если такого нет)."""
    for question in SURVEY_QUESTIONS:
        if question.key == key:
            return question
    raise KeyError(f"Нет вопроса анкеты с ключом {key!r}")


def _apply_prior(
    conn: sqlite3.Connection,
    concept_id: str,
    correct: float,
    *,
    now: float,
    settings: Settings,
) -> None:
    """Пишет слабое свидетельство самооценки: событие + обновление Beta."""
    weight = settings.self_evidence_weight
    repos.add_event(
        conn,
        Event(source="self", result=correct, concept_id=concept_id, weight=weight, ts=now),
    )
    beta.update(
        conn, concept_id, correct=correct, weight=weight, now=now, settings=settings
    )


def apply_answers(
    conn: sqlite3.Connection,
    answers: dict[str, int],
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> None:
    """Сохраняет ответы анкеты в ``facts`` и раздаёт слабый априор по концептам.

    ``answers`` — ключ вопроса → индекс выбранного варианта. Незаполненные
    вопросы просто не сохраняются (анкету можно прервать).
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    for key, index in answers.items():
        question = question_by_key(key)
        if not 0 <= index < len(question.options):
            raise ValueError(
                f"Нет варианта {index} у вопроса {key!r} (их {len(question.options)})"
            )
        option = question.options[index]
        repos.set_fact(conn, key, option.label, source="self")
        if key == GOAL_KEY and option.target_concept is not None:
            repos.set_fact(conn, GOAL_CONCEPT_KEY, option.target_concept, source="self")

    experience = answers.get(EXPERIENCE_KEY)
    if experience is not None:
        for concept_id, correct in _EXPERIENCE_PRIOR[experience].items():
            _apply_prior(conn, concept_id, correct, now=stamp, settings=s)


def is_completed(conn: sqlite3.Connection) -> bool:
    """Прошёл ли ученик анкету (по наличию факта об опыте)."""
    return repos.get_fact(conn, EXPERIENCE_KEY) is not None
