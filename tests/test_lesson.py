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
