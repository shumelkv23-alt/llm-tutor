"""Тесты меню бота."""

from llm_tutor.bot import menu


def test_main_menu_has_single_button() -> None:
    """Постоянная клавиатура — ровно одна кнопка меню."""
    kb = menu.main_menu()
    texts = [button.text for row in kb.keyboard for button in row]
    assert texts == [menu.LABEL_MENU]


def test_main_menu_is_persistent_and_resized() -> None:
    kb = menu.main_menu()
    assert kb.is_persistent is True
    assert kb.resize_keyboard is True


def test_actions_keyboard_lists_every_action() -> None:
    """В меню ровно четыре действия — по кнопке на каждое."""
    kb = menu.actions_keyboard()

    callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]
    assert callbacks == [f"menu:{action}" for action in menu.ACTION_LABELS]
    assert set(menu.ACTION_LABELS) == {"route", "themes", "close", "resume"}
