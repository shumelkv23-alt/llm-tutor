"""Тесты лестницы подсказок (Срез 5.4)."""

from llm_tutor.student.hints import MAX_HINT_LEVEL, next_hint_level


def test_rises_one_step_at_a_time() -> None:
    """Модель может попросить разбор — код поднимет только на шаг."""
    assert next_hint_level(0, 4) == 1
    assert next_hint_level(2, 4) == 3


def test_drops_freely() -> None:
    assert next_hint_level(3, 0) == 0


def test_same_level_is_kept() -> None:
    assert next_hint_level(2, 2) == 2


def test_bounds_hold_even_on_corrupted_state() -> None:
    assert next_hint_level(9, 9) == MAX_HINT_LEVEL
    assert next_hint_level(-3, -5) == 0
