"""Общие данные тестов курса из модулей (срезы 24–27)."""

from pathlib import Path

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import (
    DEFAULT_SEED_PATH,
    Course,
    load_course,
    load_course_data,
    load_seed_data,
)
from llm_tutor.db import repos
from llm_tutor.schemas import Item, Route, RouteStep, SessionState

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


def finish_all_but(conn, last: str, *, topic_id: int, completed: tuple[int, ...] = ()) -> Item:
    """Всё закрыто, кроме ``last``: по ней висит задание и серия 1.

    Темы модулей после модуля ``last`` — впереди. Возвращает висящее задание.
    """
    graph = CourseGraph.load(conn)
    boundary = graph.topic_of(last)
    steps = []
    for node in graph.topo_order():
        if node == last:
            steps.append(RouteStep(concept_id=node, mode="full", status="current"))
        elif graph.topic_of(node) <= boundary:
            steps.append(RouteStep(concept_id=node, mode="full", status="closed", closed_at=0.5))
        else:
            steps.append(RouteStep(concept_id=node, mode="full", status="ahead"))
    item = next(
        i
        for i in repos.get_items(conn)
        if i.answer_type in ("choice", "short") and last in i.concept_weights
    )
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(
            current_node_id=last,
            phase="practice",
            node_streak=1,
            pending_item_id=item.id,
            route=Route(steps=steps, topic_id=topic_id, completed_topics=list(completed)),
        ),
    )
    return item


def enter_module(conn, topic_id: int, *, completed: tuple[int, ...] = ()) -> None:
    """Модули до ``topic_id`` закрыты целиком; ученик на входе в ``topic_id``."""
    graph = CourseGraph.load(conn)
    steps = [
        RouteStep(concept_id=node, mode="full", status="closed", closed_at=0.5)
        if graph.topic_of(node) < topic_id
        else RouteStep(concept_id=node, mode="full", status="ahead")
        for node in graph.topo_order()
    ]
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(
            route=Route(steps=steps, topic_id=topic_id, completed_topics=list(completed))
        ),
    )
