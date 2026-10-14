"""Тесты детерминированной автопроверки choice/short (Срез 4.6)."""

import pytest
from pydantic import ValidationError

from llm_tutor.grader.autocheck import AutoCheckError, check
from llm_tutor.schemas import Item

OPTIONS = [
    "Двумерная таблица",
    "Одномерный массив",
    "Список словарей",
    "Файл на диске",
]


def _choice(answer: str = "0", options: list[str] | None = None) -> Item:
    return Item(
        id=1,
        prompt="?",
        answer_type="choice",
        options=options if options is not None else OPTIONS,
        answer=answer,
    )


def _short(answer: str) -> Item:
    return Item(id=2, prompt="?", answer_type="short", answer=answer)


# --- choice ---


def test_choice_correct_index_passes() -> None:
    result = check(_choice(answer="0"), "0")

    assert result.score == 1.0
    assert result.criteria[0].passed is True
    assert result.confidence == 1.0


def test_choice_wrong_index_fails() -> None:
    assert check(_choice(answer="0"), "2").score == 0.0


def test_choice_accepts_option_text() -> None:
    """Ответ можно принять и текстом варианта, не только индексом."""
    assert check(_choice(answer="0"), "Двумерная таблица").score == 1.0


def test_choice_out_of_range_index_fails() -> None:
    assert check(_choice(answer="0"), "9").score == 0.0


def test_choice_empty_answer_fails() -> None:
    assert check(_choice(answer="0"), "   ").score == 0.0


# --- short ---


def test_short_is_case_and_space_insensitive() -> None:
    assert check(_short("read_csv"), "  Read_CSV  ").score == 1.0


def test_short_accepts_call_parentheses() -> None:
    """`groupby()` и `groupby` — один и тот же ответ."""
    assert check(_short("groupby"), "groupby()").score == 1.0


def test_short_normalizes_yo() -> None:
    assert check(_short("всё"), "все").score == 1.0


def test_short_accepts_equal_numbers_in_other_notation() -> None:
    assert check(_short("1000000"), "1e6").score == 1.0


def test_short_accepts_comma_decimal_separator() -> None:
    assert check(_short("1.5"), "1,5").score == 1.0


def test_short_rejects_wrong_answer() -> None:
    assert check(_short("read_csv"), "read_excel").score == 0.0


def test_short_rejects_empty_answer() -> None:
    assert check(_short("read_csv"), "").score == 0.0


def test_short_does_not_fuzzy_match() -> None:
    """Опечатка — неверный ответ: никакого нечёткого сравнения в MVP."""
    assert check(_short("read_csv"), "readcsv").score == 0.0


def test_short_does_not_accept_nearby_large_numbers() -> None:
    """Допуск только на разницу представления, не на «примерно равно»."""
    assert check(_short("1000"), "1001").score == 0.0
    assert check(_short("2024"), "2023").score == 0.0


# --- битый эталон ---


def test_choice_with_out_of_range_reference_is_rejected_by_model() -> None:
    """Опечатка в индексе эталона не должна доехать до ученика."""
    with pytest.raises(ValidationError, match="индекс"):
        Item(id=1, prompt="?", answer_type="choice", options=["a", "b"], answer="7")


def test_choice_without_options_is_rejected_by_model() -> None:
    with pytest.raises(ValidationError, match="варианты"):
        Item(id=1, prompt="?", answer_type="choice", options=[], answer="0")


def test_short_without_reference_is_rejected_by_model() -> None:
    with pytest.raises(ValidationError, match="эталон"):
        Item(id=2, prompt="?", answer_type="short", answer="")


def test_check_raises_on_unresolvable_choice_reference() -> None:
    """Второй рубеж: строка из БД мимо валидации не даёт «неверно» за верный ответ."""
    broken = Item.model_construct(
        id=9, prompt="?", answer_type="choice", options=["a", "b"], answer="7"
    )

    with pytest.raises(AutoCheckError, match="вне вариантов"):
        check(broken, "0")


def test_check_raises_on_empty_short_reference() -> None:
    broken = Item.model_construct(id=9, prompt="?", answer_type="short", answer="")

    with pytest.raises(AutoCheckError, match="пустой эталон"):
        check(broken, "ответ")


# --- неприменимые задания ---


def test_open_item_is_not_auto_checkable() -> None:
    item = Item(id=3, prompt="?", answer_type="open", answer="эталон")

    with pytest.raises(AutoCheckError, match="answer_type"):
        check(item, "ответ")
