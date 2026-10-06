"""Тесты обработчика хода (Срез 5.5)."""

import sqlite3

import pytest

from llm_tutor.core.turn import STALE_ITEM_REPLY, handle_turn, post_turn, start_practice
from llm_tutor.course.ingest import ingest_text
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.llm.client import LLMError
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY, NO_COURSE_ANSWER
from llm_tutor.schemas import SessionState
from llm_tutor.student import beta


class _FakeTutor:
    """Подставной клиент: отвечает заготовкой и задаёт уровень подсказки."""

    def __init__(self, reply: str = "Ответ тьютора", hint_level: int = 0) -> None:
        self.reply = reply
        self.hint_level = hint_level
        self.calls: list[list] = []

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        self.calls.append(list(messages))
        return schema(reply=self.reply, hint_level=self.hint_level)

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        raise AssertionError("тьюторский ход должен идти через chat_structured")


class _FailingTutor:
    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        raise LLMError("сбой", retryable=False)


def _state(conn) -> SessionState:
    session_id = repos.ensure_open_session(conn, now=1.0)
    return repos.get_session_state(conn, session_id)


def _set_state(conn, **patch) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(conn, session_id, state.model_copy(update=patch))


# --- тьюторский путь ---


async def test_tutor_uses_material_and_persists_turn(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeTutor("Смотри на groupby")

    reply = await handle_turn(conn, client, "m", "как работает groupby?", now=1.0, settings=settings)

    assert reply == "Смотри на groupby"
    material_in_prompt = any(
        "groupby aggregates rows" in message.content for message in client.calls[0]
    )
    assert material_in_prompt
    messages = repos.get_messages(conn, repos.get_open_session(conn))
    assert [m.role for m in messages] == ["user", "assistant"]


async def test_hint_level_rises_at_most_one_step(conn, settings) -> None:
    """Один ход не может выдать разбор: код держит шаг лестницы."""
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    client = _FakeTutor(hint_level=4)

    await handle_turn(conn, client, "m", "не понимаю groupby", now=1.0, settings=settings)

    assert _state(conn).hint_level == 1  # было 0


async def test_hint_level_can_drop(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")
    _set_state(conn, hint_level=3)

    await handle_turn(conn, _FakeTutor(hint_level=0), "m", "groupby понял, дальше сам", now=1.0, settings=settings)

    assert _state(conn).hint_level == 0


async def test_offtopic_question_is_refused_without_llm(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    client = _FakeTutor("не должно вызываться")

    reply = await handle_turn(
        conn, client, "m", "how do I train a neural network?", now=1.0, settings=settings
    )

    assert reply == NO_COURSE_ANSWER
    assert client.calls == []


async def test_llm_failure_is_reported_and_turn_persisted(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## S\n\ngroupby\n", "u")

    reply = await handle_turn(conn, _FailingTutor(), "m", "groupby?", now=1.0, settings=settings)

    assert reply == LLM_FAILURE_REPLY
    assert len(repos.get_messages(conn, repos.get_open_session(conn))) == 2


# --- ответ на задание ---


async def test_answer_is_checked_without_llm(conn, settings) -> None:
    load_seed(conn)
    client = _FakeTutor("тьютор не должен вызываться")

    question = start_practice(conn, now=1.0, settings=settings)
    item = repos.get_item(conn, _state(conn).pending_item_id)
    reply = await handle_turn(conn, client, "m", item.options[0], now=2.0, settings=settings)

    assert question  # задание выдано
    assert "Верно" in reply
    assert client.calls == []  # проверял код, не модель
    assert _state(conn).pending_item_id is None
    assert any(event.source == "checked" for event in repos.get_events(conn))


async def test_wrong_answer_shows_correct_option(conn, settings) -> None:
    load_seed(conn)
    _set_state(conn, pending_item_id=1, current_node_id="pandas_intro")
    item = repos.get_item(conn, 1)

    reply = await handle_turn(conn, _FakeTutor(), "m", item.options[1], now=1.0, settings=settings)

    assert "Не совсем" in reply
    assert item.options[0] in reply
    assert beta.estimate(conn, "pandas_intro", now=1.0, settings=settings).mean < 0.5


async def test_stale_pending_item_clears_state(conn, settings) -> None:
    _set_state(conn, pending_item_id=999)

    reply = await handle_turn(conn, _FakeTutor(), "m", "0", now=1.0, settings=settings)

    assert reply == STALE_ITEM_REPLY
    assert _state(conn).pending_item_id is None
    assert len(repos.get_messages(conn, repos.get_open_session(conn))) == 2


async def test_start_practice_sets_pending_item(conn, settings) -> None:
    load_seed(conn)

    text = start_practice(conn, now=1.0, settings=settings)

    state = _state(conn)
    assert state.pending_item_id is not None
    assert state.current_node_id
    assert state.hint_level == 0
    assert text


def test_start_practice_without_graph_hints_seed(conn, settings) -> None:
    assert "seed" in start_practice(conn, now=1.0, settings=settings).lower()


# --- атомарность хода ---


def test_post_turn_rolls_back_everything_on_failure(conn, settings) -> None:
    """Сбой посреди записи не оставляет ни реплик, ни событий, ни состояния."""
    session_id = repos.ensure_open_session(conn, now=1.0)
    state = repos.get_session_state(conn, session_id)
    ghost = beta.MasteryUpdate("ghost", 1.0, 1.0, 0.0, None)  # нет такого концепта

    with pytest.raises(sqlite3.IntegrityError):
        post_turn(
            conn,
            session_id,
            user_text="вопрос",
            assistant_text="ответ",
            state=state.model_copy(update={"hint_level": 2}),
            mastery=[ghost],
            now=2.0,
        )

    assert repos.get_messages(conn, session_id) == []
    assert repos.get_session_state(conn, session_id).hint_level == 0
