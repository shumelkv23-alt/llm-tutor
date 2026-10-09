"""Тесты хендлеров бота (с замоканным LLM-клиентом)."""

from aiogram import Router

from llm_tutor.bot.handlers import (
    handle_start,
    make_router,
)
from llm_tutor.bot import start
from llm_tutor.bot.render import render_plan
from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.schemas import Concept, SessionState


class _FakeClient:
    """Подставной клиент: запоминает вызовы и возвращает заготовленный ответ."""

    def __init__(self, answer: str = "Привет!") -> None:
        self.answer = answer
        self.calls: list[tuple[list[ChatMessage], str]] = []

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        self.calls.append((list(messages), model))
        return self.answer


# --- ход с записью в БД ---


def test_handle_start_persists_session_and_messages(conn) -> None:
    """handle_start заводит сессию и пишет обе реплики в хронологии."""
    reply = handle_start(conn, "привет", now=123.0)

    session_id = repos.get_open_session(conn)
    messages = repos.get_messages(conn, session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "привет"
    assert messages[1].content == reply


def test_handle_start_updates_last_activity(conn) -> None:
    handle_start(conn, "привет", now=123.0)
    session_id = repos.get_open_session(conn)
    assert repos.get_session_state(conn, session_id).last_activity == 123.0


def test_handle_start_reuses_single_session(conn) -> None:
    """Повторный /start не плодит сессии — открытая переиспользуется."""
    handle_start(conn, "раз", now=1.0)
    handle_start(conn, "два", now=2.0)

    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    session_id = repos.get_open_session(conn)
    assert len(repos.get_messages(conn, session_id)) == 4


def test_welcome_back_names_current_node_without_model_tag(conn) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(current_node_id="groupby"))

    reply = handle_start(conn, "привет", now=2.0)

    assert "С возвращением" in reply
    assert "Группировка" in reply
    assert "[модель:" not in reply


def test_welcome_back_without_node_invites_to_write(conn) -> None:
    load_seed(conn)

    reply = handle_start(conn, "привет", now=2.0)

    assert reply == start.WELCOME_BACK_IDLE


def test_state_survives_restart(tmp_path) -> None:
    """Состояние и сессия переживают переоткрытие БД («рестарт» бота)."""
    db_file = str(tmp_path / "tutor.db")

    first_conn = get_conn(db_file)
    migrate(first_conn)
    handle_start(first_conn, "привет", now=10.0)
    session_id = repos.get_open_session(first_conn)
    first_conn.close()

    second_conn = get_conn(db_file)
    migrate(second_conn)  # повторный migrate на существующей схеме
    try:
        assert repos.get_open_session(second_conn) == session_id
        assert repos.get_session_state(second_conn, session_id).last_activity == 10.0
        assert len(repos.get_messages(second_conn, session_id)) == 2
    finally:
        second_conn.close()


# --- маршрут (/plan) ---


def test_render_plan_lists_steps_with_marks(conn, settings) -> None:
    """Маршрут — нумерованный список с пометкой текущего узла."""
    load_seed(conn)
    repos.set_fact(conn, "goal_concept_id", "summary_tables")
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn, session_id, SessionState(current_node_id="groupby")
    )

    text = render_plan(conn, now=0.0, settings=settings)

    assert "<pre>" not in text and "┌" not in text
    assert "Маршрут" in text
    assert "1. " in text
    assert "[>]" in text            # метка текущего узла


def test_render_plan_escapes_concept_names(conn, settings) -> None:
    """Имя узла с HTML-спецсимволом экранируется — иначе сломается parse_mode=HTML."""
    load_seed(conn)
    repos.upsert_concept(conn, Concept(id="x_amp", name="A & B"))
    repos.set_fact(conn, "goal_concept_id", "x_amp")
    repos.ensure_open_session(conn, now=1.0)

    text = render_plan(conn, now=0.0, settings=settings)

    assert "A &amp; B" in text   # экранированная форма доходит до ячейки/шапки
    assert "A & B" not in text   # сырой амперсанд в HTML не просачивается


def test_render_plan_lists_every_step(conn, settings) -> None:
    """Маршрут показывается целиком: список, а не окно вокруг текущего узла."""
    from llm_tutor.course.graph import CourseGraph
    from llm_tutor.student import route as route_mod

    load_seed(conn)
    repos.set_fact(conn, "goal_concept_id", "churn_eda_case")
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn, session_id, SessionState(current_node_id="read_csv")
    )

    text = render_plan(conn, now=0.0, settings=settings)

    graph = CourseGraph.load(conn)
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id="churn_eda_case",
        current_node_id="read_csv",
        now=0.0,
        settings=settings,
    )
    for step in route.steps:
        assert graph.concept(step.concept_id).name in text


def test_render_plan_reports_all_mastered(conn, settings) -> None:
    """Когда весь граф освоен, /plan не врёт про незакрытые пререквизиты."""
    seed = load_seed(conn)
    for concept in seed.nodes:
        repos.upsert_mastery(
            conn, concept.id, alpha=38.0, beta=2.0, last_seen=0.0, next_review=1e9
        )

    text = render_plan(conn, now=0.0, settings=settings)

    assert "Курс пройден" in text


def test_render_plan_empty_graph_hints_seed(conn, settings) -> None:
    text = render_plan(conn, now=0.0, settings=settings)

    assert "seed" in text.lower()


def test_make_router_registers_start_handler(conn) -> None:
    """make_router возвращает роутер с зарегистрированным /start."""
    router = make_router(conn, _FakeClient(), "m")
    assert isinstance(router, Router)
    assert router.message.handlers


def test_menu_handlers_are_registered(conn) -> None:
    """Кнопка меню и разбор действий реально подключены к роутеру."""
    router = make_router(conn, _FakeClient(), "m")
    names = [h.callback.__name__ for h in router.message.handlers]
    assert "on_menu" in names
    callbacks = [h.callback.__name__ for h in router.callback_query.handlers]
    assert "on_menu_action" in callbacks


async def test_welcome_back_names_module_and_topic(conn, settings) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(current_node_id="groupby"))

    text = start.welcome_back_text(conn)

    name = CourseGraph.load(conn).concept("groupby").name
    assert "Модуль 1" in text and f"«{name}»" in text


async def test_welcome_back_names_working_module_like_plan(conn, settings) -> None:
    """F-4: тема модуля 1 в участке модуля 2 — «С возвращением» и /plan про модуль 2."""
    from course_fixtures import load_two_modules
    from llm_tutor.schemas import Route, RouteStep

    load_two_modules(conn)
    graph = CourseGraph.load(conn)
    steps = [RouteStep(concept_id=n, mode="full", status="ahead") for n in graph.topo_order()]
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="pandas_series", route=Route(steps=steps, topic_id=2)),
    )

    text = start.welcome_back_text(conn)

    assert "Модуль 2" in text
    assert "Модуль 2/" in render_plan(conn, settings=settings)
