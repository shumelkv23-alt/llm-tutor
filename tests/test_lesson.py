"""Урок за ручку (срез 28): «ответ или реплика», части урока."""

import pytest

from llm_tutor.core import lesson
from llm_tutor.schemas import Item

CHOICE = Item(
    id=1,
    prompt="Что вернёт df.iloc[0]?",
    answer_type="choice",
    concept_weights={"x": 1.0},
    difficulty=0.3,
    options=["Первую строку", "Первый столбец", "Индекс", "150"],
    answer="0",
)
SHORT = Item(
    id=2,
    prompt="Какой параметр ограничивает глубину?",
    answer_type="short",
    concept_weights={"x": 1.0},
    difficulty=0.3,
    answer="max_depth",
)


@pytest.mark.parametrize(
    ("item", "text", "kind"),
    [
        (CHOICE, "2", "answer"),
        (CHOICE, "первую строку", "answer"),
        (CHOICE, "150", "answer"),
        (CHOICE, "а что такое индекс", "chat"),
        (CHOICE, "подожди, я про другое", "chat"),
        (CHOICE, "7", "chat"),
        (SHORT, "max_depth", "answer"),
        (SHORT, "0.25", "answer"),
        (SHORT, "зачем это нужно", "chat"),
        (SHORT, "а это вообще про деревья или про лес", "chat"),
        (SHORT, "max_depth?", "chat"),
    ],
)
def test_answer_kind(item: Item, text: str, kind: str) -> None:
    assert lesson.answer_kind(item, text) == kind


# --- части урока и «Дальше» ---

from test_turn import _FailingTutor, _FakeTutor, _state  # noqa: E402

from llm_tutor.core.turn import handle_turn, lesson_next_reply  # noqa: E402
from llm_tutor.course.seed import load_seed  # noqa: E402


def test_split_parts() -> None:
    assert lesson.split_parts("A\n---\nB\n---\nC") == ["A", "B", "C"]
    assert lesson.split_parts("Только одна часть") == ["Только одна часть"]
    assert lesson.split_parts("A\n---\n\n---\nB") == ["A", "B"]
    assert lesson.split_parts("1\n---\n2\n---\n3\n---\n4") == ["1", "2", "3\n\n4"]


async def test_entering_node_shows_first_part_without_task(conn, settings) -> None:
    load_seed(conn)

    reply = await handle_turn(
        conn, _FakeTutor("A\n---\nB\n---\nC"), "m", "Поехали", now=1.0, settings=settings
    )

    node = _state(conn).current_node_id
    assert reply.text == "A"
    assert reply.button == (lesson.NEXT_LABEL, f"lesson:next:{node}:1")
    assert reply.tail is None and _state(conn).pending_item_id is None


async def test_next_parts_then_check_question(conn, settings) -> None:
    load_seed(conn)
    await handle_turn(
        conn, _FakeTutor("A\n---\nB\n---\nC"), "m", "Поехали", now=1.0, settings=settings
    )
    node = _state(conn).current_node_id

    second = lesson_next_reply(conn, node, 1, now=2.0, settings=settings)
    third = lesson_next_reply(conn, node, 2, now=3.0, settings=settings)
    check = lesson_next_reply(conn, node, 3, now=4.0, settings=settings)

    assert second.text == "B" and second.button[0] == lesson.NEXT_LABEL
    assert third.text == "C" and third.button == (lesson.CHECK_LABEL, f"lesson:next:{node}:3")
    assert _state(conn).pending_item_id is not None  # «Проверим» выдал вопрос
    assert check.button is None


async def test_stale_next_button_changes_nothing(conn, settings) -> None:
    load_seed(conn)
    await handle_turn(
        conn, _FakeTutor("A\n---\nB\n---\nC"), "m", "Поехали", now=1.0, settings=settings
    )
    node = _state(conn).current_node_id
    lesson_next_reply(conn, node, 1, now=2.0, settings=settings)
    before = _state(conn)

    again = lesson_next_reply(conn, node, 1, now=3.0, settings=settings)
    alien = lesson_next_reply(conn, "no_such_node", 2, now=3.0, settings=settings)

    assert again.text == lesson.STALE_PART_REPLY == alien.text
    assert _state(conn).lesson_part == before.lesson_part


async def test_free_text_mid_lesson_answers_without_task(conn, settings) -> None:
    load_seed(conn)
    await handle_turn(
        conn, _FakeTutor("A\n---\nB"), "m", "Поехали", now=1.0, settings=settings
    )
    node = _state(conn).current_node_id

    reply = await handle_turn(
        conn, _FakeTutor("потому что"), "m", "а зачем это", now=2.0, settings=settings
    )

    assert "потому что" in reply.text
    assert _state(conn).pending_item_id is None
    assert reply.button == (lesson.NEXT_LABEL, f"lesson:next:{node}:1")


async def test_model_failure_falls_back_to_description(conn, settings) -> None:
    load_seed(conn)

    reply = await handle_turn(conn, _FailingTutor(), "m", "Поехали", now=1.0, settings=settings)

    assert reply.button is not None and reply.button[0] == lesson.CHECK_LABEL
    assert reply.text  # описание темы из графа, а не пустота
