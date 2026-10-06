"""Тесты тонкого репозитория (CRUD поверх SQLite)."""

import sqlite3

import pytest

from llm_tutor.db import repos
from llm_tutor.schemas import Chunk, Event, Item, SessionState


def _add_concept(conn, concept_id: str) -> None:
    """Заводит концепт — нужен для FK из events/mastery."""
    conn.execute("INSERT INTO concepts (id, name) VALUES (?, ?)", (concept_id, concept_id))
    conn.commit()


# --- sessions ---


def test_ensure_open_session_reuses_existing(conn) -> None:
    first = repos.ensure_open_session(conn, now=100.0)
    second = repos.ensure_open_session(conn, now=200.0)
    assert first == second


def test_end_session_then_new_session_created(conn) -> None:
    old = repos.ensure_open_session(conn, now=100.0)
    repos.end_session(conn, old, now=150.0)
    assert repos.get_open_session(conn) is None

    new = repos.ensure_open_session(conn, now=200.0)
    assert new != old


def test_session_state_roundtrip(conn) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    assert repos.get_session_state(conn, session_id) == SessionState()

    state = SessionState(hint_level=3, last_activity=42.0)
    repos.update_session_state(conn, session_id, state)
    assert repos.get_session_state(conn, session_id) == state


def test_get_session_state_missing_raises(conn) -> None:
    with pytest.raises(KeyError):
        repos.get_session_state(conn, 999)


def test_end_session_missing_raises(conn) -> None:
    with pytest.raises(KeyError):
        repos.end_session(conn, 999)


def test_end_session_is_idempotent(conn) -> None:
    """Повторное закрытие не перезаписывает исходное время."""
    session_id = repos.ensure_open_session(conn, now=100.0)
    repos.end_session(conn, session_id, now=150.0)
    repos.end_session(conn, session_id, now=999.0)

    ended = conn.execute(
        "SELECT ended_at FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()["ended_at"]
    assert ended == 150.0


# --- messages ---


def test_messages_roundtrip_and_chronological_order(conn) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.add_message(conn, session_id, "user", "первый", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "второй", ts=2.0)

    messages = repos.get_messages(conn, session_id)
    assert [m.content for m in messages] == ["первый", "второй"]
    assert [m.role for m in messages] == ["user", "assistant"]


def test_add_message_returns_positive_id(conn) -> None:
    session_id = repos.ensure_open_session(conn, now=1.0)
    message_id = repos.add_message(conn, session_id, "user", "x", ts=1.0)
    assert message_id > 0


def test_add_message_rejects_invalid_role(conn) -> None:
    """Мусорная роль падает на записи, а не ломает чтение всей сессии позже."""
    session_id = repos.ensure_open_session(conn, now=1.0)

    with pytest.raises(ValueError, match="роль"):
        repos.add_message(conn, session_id, "tutor", "x", ts=1.0)


def test_update_session_state_missing_raises(conn) -> None:
    with pytest.raises(KeyError):
        repos.update_session_state(conn, 999, SessionState())


def test_get_messages_returns_id_and_meta(conn) -> None:
    """Чтение реплик возвращает id и meta (без потери метаданных вердикта)."""
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.add_message(conn, session_id, "assistant", "ответ", ts=1.0, meta='{"verdict": 1}')

    message = repos.get_messages(conn, session_id)[0]
    assert message.id is not None
    assert message.meta == '{"verdict": 1}'


# --- events ---


def test_events_roundtrip_and_filter_by_concept(conn) -> None:
    _add_concept(conn, "c1")
    _add_concept(conn, "c2")
    repos.add_event(
        conn, Event(source="rubric", result=1.0, concept_id="c1", citation="q"), now=1.0
    )
    repos.add_event(conn, Event(source="checked", result=0.0, concept_id="c2"), now=2.0)

    assert len(repos.get_events(conn)) == 2
    only_c1 = repos.get_events(conn, concept_id="c1")
    assert len(only_c1) == 1
    assert only_c1[0].concept_id == "c1"
    assert only_c1[0].citation == "q"


# --- mastery ---


def test_mastery_upsert_inserts_then_updates(conn) -> None:
    _add_concept(conn, "c1")
    assert repos.get_mastery(conn, "c1") is None

    repos.upsert_mastery(conn, "c1", alpha=2.0, beta=1.0, last_seen=5.0)
    assert repos.get_mastery(conn, "c1")["alpha"] == 2.0

    repos.upsert_mastery(conn, "c1", alpha=3.0, beta=4.0)
    updated = repos.get_mastery(conn, "c1")
    assert updated["alpha"] == 3.0
    assert updated["beta"] == 4.0


def test_upsert_mastery_preserves_unset_fields(conn) -> None:
    """Не переданные last_seen/next_review сохраняются (не затираются в NULL)."""
    _add_concept(conn, "c1")
    repos.upsert_mastery(conn, "c1", alpha=2.0, beta=1.0, last_seen=5.0, next_review=99.0)

    repos.upsert_mastery(conn, "c1", alpha=3.0, beta=4.0)  # без last_seen/next_review
    mastery = repos.get_mastery(conn, "c1")
    assert mastery["alpha"] == 3.0
    assert mastery["beta"] == 4.0
    assert mastery["last_seen"] == 5.0
    assert mastery["next_review"] == 99.0


# --- facts ---


def test_fact_set_and_overwrite(conn) -> None:
    assert repos.get_fact(conn, "goal") is None

    repos.set_fact(conn, "goal", "научиться pandas")
    assert repos.get_fact(conn, "goal") == "научиться pandas"

    repos.set_fact(conn, "goal", "другое", source="self", confidence=0.9)
    assert repos.get_fact(conn, "goal") == "другое"


def test_set_fact_preserves_unset_fields(conn) -> None:
    """Перезапись без source/confidence сохраняет прежние (COALESCE)."""
    repos.set_fact(conn, "goal", "x", source="self", confidence=0.8)
    repos.set_fact(conn, "goal", "y")  # без source/confidence

    assert repos.get_fact(conn, "goal") == "y"
    row = conn.execute(
        "SELECT source, confidence FROM facts WHERE key = 'goal'"
    ).fetchone()
    assert row["source"] == "self"
    assert row["confidence"] == 0.8


# --- chunks ---


def test_replace_chunks_swaps_source_chunks(conn) -> None:
    repos.replace_chunks(conn, "u", [Chunk(source_url="u", seq=0, content="old")])

    count = repos.replace_chunks(conn, "u", [Chunk(source_url="u", seq=0, content="new")])

    assert count == 1
    assert [r["content"] for r in conn.execute("SELECT content FROM chunks")] == ["new"]


def test_replace_chunks_rolls_back_on_failure(conn) -> None:
    """Сбой вставки откатывает и удаление — чанки источника не теряются."""
    repos.replace_chunks(conn, "u", [Chunk(source_url="u", seq=0, content="old")])

    with pytest.raises(sqlite3.IntegrityError):  # FK: концепта 'ghost' нет
        repos.replace_chunks(
            conn,
            "u",
            [Chunk(source_url="u", seq=0, content="new", concept_id="ghost")],
        )

    assert [r["content"] for r in conn.execute("SELECT content FROM chunks")] == ["old"]


def test_get_items_skips_corrupt_row(conn) -> None:
    """Одна битая строка не должна лишать ученика всего банка заданий."""
    repos.replace_seed(conn, [], [], [])
    repos.upsert_item(conn, Item(id=1, prompt="?", answer_type="short", answer="ок"))
    # Строка в обход валидатора: choice без вариантов и с эталоном вне диапазона
    conn.execute(
        "INSERT INTO items (id, prompt, answer_type, options, answer, concept_weights) "
        "VALUES (900, '?', 'choice', '[]', '7', '{}')"
    )
    conn.commit()

    ids = {item.id for item in repos.get_items(conn)}

    assert ids == {1}


# --- параметризация ---


def test_special_characters_in_values_do_not_break_sql(conn) -> None:
    """Значения со спецсимволами уходят как данные, а не как SQL."""
    session_id = repos.ensure_open_session(conn, now=1.0)
    payload = "1'; DROP TABLE messages; --"
    repos.add_message(conn, session_id, "user", payload, ts=1.0)

    assert repos.get_messages(conn, session_id)[0].content == payload
    # инъекция не выполнилась — таблица на месте
    assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
