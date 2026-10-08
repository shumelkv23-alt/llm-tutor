"""Общие данные тестов курса из модулей (срезы 24–27)."""

from pathlib import Path

from llm_tutor.course.seed import (
    DEFAULT_SEED_PATH,
    Course,
    load_course,
    load_course_data,
    load_seed_data,
)
from llm_tutor.schemas import Item

# Топологический порядок topic01 до появления модулей (снят на коммите e54f3d0):
# переход на модули не должен его сдвинуть.
TOPIC01_ORDER = [
    "python_basics",
    "numpy_basics",
    "pandas_intro",
    "pandas_series",
    "pandas_dataframe",
    "groupby",
    "indexing_loc_iloc",
    "read_csv",
    "sorting",
    "value_counts",
    "agg_functions",
    "boolean_indexing",
    "df_inspect",
    "summary_tables",
    "apply_functions",
    "describe_stats",
    "dtype_conversion",
    "df_transformations",
    "visualization_basics",
    "eda_workflow",
    "churn_eda_case",
]

MINI_SEED_PATH = Path(__file__).parent / "fixtures" / "seed_topic02_mini.json"

# Анкеты модулей: topic01 и тестового мини-модуля 2.
T1 = load_seed_data(DEFAULT_SEED_PATH).topic.survey
T2 = load_course_data([DEFAULT_SEED_PATH, MINI_SEED_PATH]).topics[1].survey


def load_two_modules(conn) -> Course:
    """Курс «topic01 + мини-модуль 2» в БД — как бот на старте."""
    return load_course(conn, [DEFAULT_SEED_PATH, MINI_SEED_PATH])


def correct_answer(item: Item) -> str:
    """Текст верного ответа на задание с автопроверкой."""
    return item.options[int(item.answer)] if item.answer_type == "choice" else item.answer
