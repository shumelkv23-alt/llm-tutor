"""Telegram-хендлеры бота.

Зависимости (LLM-клиент и модель) внедряются через фабрику `make_router`,
чтобы логику можно было тестировать без живого aiogram.
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.schemas import ChatMessage

START_SYSTEM_PROMPT = (
    "Ты — тьютор по курсу машинного обучения (mlcourse.ai). "
    "Отвечай кратко, по-дружески и по делу."
)

# Telegram отклоняет сообщения длиннее 4096 символов — оставляем запас.
MAX_REPLY_LENGTH = 4000


async def build_start_reply(client: LLMClient, model: str, user_text: str) -> str:
    """Логика ответа на /start: вызов модели + подпись с именем модели.

    Сбои LLM ловятся здесь, чтобы ученик не получал молчание.
    """
    messages = [
        ChatMessage(role="system", content=START_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_text or "Привет!"),
    ]
    try:
        answer = await client.chat(messages, model=model)
    except LLMError:
        return "Не смог получить ответ от модели — попробуй ещё раз чуть позже."

    reply = f"[модель: {model}]\n\n{answer}"
    if len(reply) > MAX_REPLY_LENGTH:
        return reply[:MAX_REPLY_LENGTH] + "…"
    return reply


def make_router(client: LLMClient, model: str) -> Router:
    """Собирает роутер с внедрёнными зависимостями (client, model)."""
    router = Router()

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        reply = await build_start_reply(client, model, message.text or "")
        await message.answer(reply)

    return router
