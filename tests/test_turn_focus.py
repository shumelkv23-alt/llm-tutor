"""Ход с выделенным фрагментом: намерения, задание, журнал (срез 76)."""

import json

import pytest
from legacy_course import load_seed
from test_turn import ALL_BLOCKS, _claim, _FailingTutor, _FakeTutor, _set_state, _state

from llm_tutor.core import intents
from llm_tutor.core.context import FOCUS_QUESTIONS, FOCUS_RULE, Focus, focus_from_meta
from llm_tutor.core.turn import SKIP_REPLY, handle_turn, start_practice_reply
from llm_tutor.course.ingest import ingest_text
from llm_tutor.db import repos
from llm_tutor.llm.prompts import LLM_FAILURE_REPLY

TASK = Focus("task", "найдите среднее по столбцу")


def _messages(conn):
    return repos.get_messages(conn, repos.get_open_session(conn))


def _last_user(conn):
    return [m for m in _messages(conn) if m.role == "user"][-1]


async def test_focus_reaches_model_and_meta(conn, settings) -> None:
    load_seed(conn)
    start_practice_reply(conn, now=1.0, settings=settings)
    pending = _state(conn).pending_item_id
    client = _FakeTutor("объясняю")

    await handle_turn(
        conn, client, "m", "почему так?", now=2.0, settings=settings,
        answer_mode="tutor", focus=TASK,
    )

    prompt = client.calls[0][-1].content
    assert "из текста задания" in prompt and FOCUS_RULE in prompt
    assert _state(conn).pending_item_id == pending
    assert repos.get_events(conn) == []
    user = _last_user(conn)
    assert user.content == "почему так?"
    assert focus_from_meta(user.meta) == TASK
    assistant = _messages(conn)[-1]
    assert focus_from_meta(assistant.meta) is None


async def test_skip_inside_fragment_keeps_task(conn, settings) -> None:
    load_seed(conn)
    start_practice_reply(conn, now=1.0, settings=settings)
    pending = _state(conn).pending_item_id
    client = _FakeTutor("объясняю")

    await handle_turn(
        conn, client, "m", "что это значит?", now=2.0, settings=settings,
        answer_mode="tutor", focus=Focus("theory", "пропусти"),
    )

    assert _state(conn).pending_item_id == pending
    assert len(client.calls) == 1


async def test_skip_in_question_with_fragment_skips(conn, settings) -> None:
    load_seed(conn)
    start_practice_reply(conn, now=1.0, settings=settings)
    client = _FakeTutor("тьютор не должен вызываться")

    reply = await handle_turn(
        conn, client, "m", "пропусти", now=2.0, settings=settings,
        answer_mode="tutor", focus=TASK,
    )

    assert reply.text == SKIP_REPLY
    assert _state(conn).pending_item_id is None
    assert client.calls == []
    assert focus_from_meta(_last_user(conn).meta) == TASK


async def _entering(conn, settings) -> None:
    await handle_turn(
        conn, _FakeTutor(), "m", "давай учиться", now=1.0, settings=settings, focus=TASK
    )


async def _close_phrase(conn, settings) -> None:
    _set_state(conn, current_node_id="groupby", pending_item_id=1)
    await handle_turn(
        conn, _FakeTutor(), "m", "закрой тему", now=1.0, settings=settings,
        answer_mode="tutor", focus=TASK,
    )
    assert _state(conn).mode == "verify"


async def _close_flag(conn, settings) -> None:
    ingest_text(conn, "# T\n\n## Grouping\n\ngroupby aggregates rows\n", "u")
    _set_state(conn, current_node_id="groupby")
    client = _FakeTutor("Держишь тему уверенно", wants_close_topic=True)
    await handle_turn(
        conn, client, "m", "я тут всё знаю уже", now=1.0, settings=settings,
        answer_mode="tutor", focus=TASK,
    )
    assert _state(conn).mode == "verify"


async def _claimed(conn, settings) -> None:
    _claim(conn, settings, *ALL_BLOCKS)
    await handle_turn(conn, _FakeTutor(), "m", "привет", now=1.0, settings=settings, focus=TASK)
    assert _state(conn).mode == "verify"


@pytest.mark.parametrize("branch", [_entering, _close_phrase, _close_flag, _claimed])
async def test_focus_is_journaled_on_every_branch(conn, settings, branch) -> None:
    load_seed(conn)

    await branch(conn, settings)

    assert focus_from_meta(_last_user(conn).meta) == TASK


async def test_llm_failure_keeps_quote(conn, settings) -> None:
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")

    reply = await handle_turn(
        conn, _FailingTutor(), "m", "почему так?", now=1.0, settings=settings,
        answer_mode="tutor", focus=TASK,
    )

    assert reply.text == LLM_FAILURE_REPLY
    assert focus_from_meta(_last_user(conn).meta) == TASK


@pytest.mark.parametrize("question", FOCUS_QUESTIONS)
def test_button_questions_are_not_intents(question) -> None:
    assert intents.detect(question) is None


async def test_turn_without_focus_writes_no_user_meta(conn, settings) -> None:
    load_seed(conn)
    _set_state(conn, current_node_id="groupby")

    await handle_turn(
        conn, _FakeTutor(), "m", "почему так?", now=1.0, settings=settings, answer_mode="tutor"
    )

    assert _last_user(conn).meta is None


async def test_task_chars_stay_on_tutor_reply(conn, settings) -> None:
    """Фрагмент не вытесняет task_chars реплики тьютора, выдавшей задание."""
    load_seed(conn)
    _set_state(conn, current_node_id="groupby", pending_item_id=1)

    await handle_turn(
        conn, _FakeTutor(), "m", "закрой тему", now=1.0, settings=settings,
        answer_mode="tutor", focus=TASK,
    )

    assistant = _messages(conn)[-1]
    assert assistant.role == "assistant"
    assert json.loads(assistant.meta)["task_chars"] > 0
    assert "quote" not in json.loads(assistant.meta)
