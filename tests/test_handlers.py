"""Тесты хендлеров бота (с замоканным LLM-клиентом)."""

import pytest
from aiogram import Router

from llm_tutor.bot.handlers import (
    build_start_reply,
    handle_start,
    make_router,
)
from llm_tutor.bot.render import render_plan
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.llm.client import LLMError
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


class _FailingClient:
    async def chat(self, messages, *, model=None, temperature=None) -> str:
        raise LLMError("сбой", retryable=False)


class _CrashingClient:
    """Падает не-LLMError'ом — эмулирует неожиданный сбой на ходу."""

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        raise RuntimeError("неожиданный сбой")


# --- чистый ответ ---


async def test_build_start_reply_contains_model_name() -> None:
    """Ответ /start подписан именем модели (видно абстракцию модели)."""
    client = _FakeClient("Привет!")
    reply = await build_start_reply(client, "anthropic/claude-sonnet-4.5", "hi")

    assert "anthropic/claude-sonnet-4.5" in reply
    assert "Привет!" in reply


async def test_build_start_reply_builds_system_and_user_messages() -> None:
    """Клиент получает system-промпт и текст ученика."""
    client = _FakeClient()
    await build_start_reply(client, "m", "учу pandas")

    messages, model = client.calls[0]
    assert model == "m"
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[1].content == "учу pandas"


async def test_build_start_reply_catches_llm_error() -> None:
    """Сбой LLM не роняет бота — ученик получает понятный ответ."""
    reply = await build_start_reply(_FailingClient(), "m", "hi")
    assert "не смог получить ответ" in reply.lower()


async def test_build_start_reply_truncates_long_answer() -> None:
    """Ответ длиннее лимита Telegram режется до безопасной длины."""
    client = _FakeClient("а" * 5000)
    reply = await build_start_reply(client, "m", "hi")
    assert len(reply) <= 4001  # 4000 + многоточие
    assert reply.endswith("…")


# --- ход с записью в БД ---


async def test_handle_start_persists_session_and_messages(conn) -> None:
    """handle_start заводит сессию и пишет обе реплики в хронологии."""
    reply = await handle_start(conn, _FakeClient("Ответ"), "m", "привет", now=123.0)

    session_id = repos.get_open_session(conn)
    messages = repos.get_messages(conn, session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "привет"
    assert messages[1].content == reply


async def test_handle_start_updates_last_activity(conn) -> None:
    """last_activity в состоянии сессии обновляется на момент хода."""
    await handle_start(conn, _FakeClient(), "m", "привет", now=123.0)
    session_id = repos.get_open_session(conn)
    assert repos.get_session_state(conn, session_id).last_activity == 123.0


async def test_handle_start_reuses_single_session(conn) -> None:
    """Повторный /start не плодит сессии — открытая переиспользуется."""
    client = _FakeClient()
    await handle_start(conn, client, "m", "раз", now=1.0)
    await handle_start(conn, client, "m", "два", now=2.0)

    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    session_id = repos.get_open_session(conn)
    assert len(repos.get_messages(conn, session_id)) == 4


async def test_handle_start_leaves_no_orphan_on_unexpected_error(conn) -> None:
    """Неожиданный сбой (не LLMError) не оставляет реплику ученика без ответа."""
    with pytest.raises(RuntimeError):
        await handle_start(conn, _CrashingClient(), "m", "привет", now=1.0)

    assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
    assert repos.get_open_session(conn) is None


async def test_state_survives_restart(tmp_path) -> None:
    """Состояние и сессия переживают переоткрытие БД («рестарт» бота)."""
    db_file = str(tmp_path / "tutor.db")

    first_conn = get_conn(db_file)
    migrate(first_conn)
    await handle_start(first_conn, _FakeClient(), "m", "привет", now=10.0)
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

    assert "освоено" in text


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
