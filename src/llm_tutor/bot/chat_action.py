"""«Печатает…» на время долгого ответа: ученик видит, что бот не завис."""

from contextlib import AbstractAsyncContextManager, nullcontext

from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender


def typing_action(message: Message) -> AbstractAsyncContextManager:
    """Контекст «печатает…» в чате сообщения.

    ``ChatActionSender`` повторяет действие каждые 5 секунд, пока ждём модель.
    Без бота (сообщение не привязано к нему — например, в тестах) — пустой
    контекст: индикатор не важнее ответа.
    """
    bot = getattr(message, "bot", None)
    if bot is None:
        return nullcontext()
    return ChatActionSender.typing(bot=bot, chat_id=message.chat.id)
