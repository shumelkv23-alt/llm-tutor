"""Тесты хендлеров бота (с замоканным LLM-клиентом)."""

import pytest
from aiogram import Router

from llm_tutor.bot.handlers import (
    build_start_reply,
    handle_message,
    handle_start,
    make_router,
    render_plan,
)
from llm_tutor.course.ingest import ingest_text
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.llm.client import LLMError
from llm_tutor.llm.prompts import NO_COURSE_ANSWER
from llm_tutor.llm.schemas import ChatMessage


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


# --- ход на свободный вопрос (RAG) ---


async def test_handle_message_uses_rag_material(conn) -> None:
    """Найденный материал уходит в промпт, ответ берётся от модели."""
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeClient("Ответ по материалу")

    reply = await handle_message(conn, client, "m", "как работает groupby?", now=1.0)

    assert reply == "Ответ по материалу"
    messages, _ = client.calls[0]
    assert any("groupby aggregates rows" in m.content for m in messages)


async def test_handle_message_short_circuits_without_material(conn) -> None:
    """Нет материала — детерминированное «нет в курсе», LLM не вызывается."""
    client = _FakeClient()

    reply = await handle_message(conn, client, "m", "что такое DataFrame?", now=1.0)

    assert reply == NO_COURSE_ANSWER
    assert client.calls == []


async def test_handle_message_persists_turn(conn) -> None:
    ingest_text(conn, "# T\n\n## S\n\ngroupby aggregation\n", "u")
    client = _FakeClient("ответ")

    await handle_message(conn, client, "m", "вопрос про groupby", now=1.0)

    session_id = repos.get_open_session(conn)
    messages = repos.get_messages(conn, session_id)
    assert [m.role for m in messages] == ["user", "assistant"]


async def test_handle_message_catches_llm_error(conn) -> None:
    ingest_text(conn, "# T\n\n## S\n\ngroupby aggregation\n", "u")

    reply = await handle_message(conn, _FailingClient(), "m", "groupby?", now=1.0)

    assert "не смог получить ответ" in reply.lower()


async def test_handle_message_greeting_reaches_llm(conn) -> None:
    """Приветствие — это реплика диалога, а не вопрос вне курса."""
    client = _FakeClient("Привет! Чем помочь по Pandas?")

    reply = await handle_message(conn, client, "m", "привет!", now=1.0)

    assert reply == "Привет! Чем помочь по Pandas?"
    assert len(client.calls) == 1


async def test_handle_message_greeting_uses_no_material_prompt(conn) -> None:
    """Без материала модель получает промпт, запрещающий выдумывать факты курса."""
    client = _FakeClient()

    await handle_message(conn, client, "m", "спасибо!", now=1.0)

    messages, _ = client.calls[0]
    assert messages[0].role == "system"
    assert "фрагментов материалов курса нет" in messages[0].content


async def test_handle_message_offtopic_not_in_course(conn) -> None:
    """Материал в базе есть, но вопрос вне темы → «не нашёл», без вызова LLM."""
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeClient("не должно вызываться")

    reply = await handle_message(
        conn, client, "m", "how do I train a neural network?", now=1.0
    )

    assert reply == NO_COURSE_ANSWER
    assert client.calls == []


async def test_handle_message_survives_unexpected_error(conn) -> None:
    """Неожиданный сбой LLM не оставляет ученика без ответа."""
    ingest_text(conn, "# T\n\n## S\n\ngroupby aggregation\n", "u")

    reply = await handle_message(conn, _CrashingClient(), "m", "groupby?", now=1.0)

    assert "не смог получить ответ" in reply.lower()
    session_id = repos.get_open_session(conn)
    assert len(repos.get_messages(conn, session_id)) == 2


# --- сборка роутера ---


# --- маршрут (/plan) ---


def test_render_plan_lists_ready_node_with_mode(conn, settings) -> None:
    """На холодном старте готов корневой узел — с режимом сжатого прохода."""
    load_seed(conn)

    text = render_plan(conn, now=0.0, settings=settings)

    assert "Основы Python" in text
    assert "сжатый проход" in text


def test_render_plan_empty_graph_hints_seed(conn, settings) -> None:
    text = render_plan(conn, now=0.0, settings=settings)

    assert "seed" in text.lower()


def test_make_router_registers_start_handler(conn) -> None:
    """make_router возвращает роутер с зарегистрированным /start."""
    router = make_router(conn, _FakeClient(), "m")
    assert isinstance(router, Router)
    assert router.message.handlers
