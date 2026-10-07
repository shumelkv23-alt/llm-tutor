"""Меню бота: постоянная кнопка и всплывающий список действий.

Reply-клавиатура несёт ровно одну кнопку «☰ Меню». По нажатию бот присылает
инлайн-список действий (callback `menu:<action>`) — функционал виден, но
чтобы не загораживать переписку кнопками.
"""

from typing import Literal

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

Action = Literal["status", "route", "themes", "task", "stuck", "skip", "help"]

LABEL_MENU = "☰ Меню"
MENU_TITLE = "Что сделать?"

# Действие → подпись кнопки в инлайн-списке (порядок задаёт порядок кнопок).
ACTION_LABELS: dict[Action, str] = {
    "status": "📚 Моё обучение",
    "route": "🗺 Маршрут",
    "themes": "🎚 Темы",
    "task": "🎯 Задание",
    "stuck": "❓ Не понимаю",
    "skip": "⏭ Пропустить",
    "help": "ℹ️ Что умею",
}


def main_menu() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура: одна кнопка меню (не сворачивается)."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=LABEL_MENU)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def actions_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-список действий меню (по 2 в ряду)."""
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"menu:{action}")
        for action, label in ACTION_LABELS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)
