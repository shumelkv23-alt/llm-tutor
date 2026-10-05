"""Telegram-хендлеры бота.

Зависимости (LLM-клиент и модель) внедряются через фабрику `make_router`,
чтобы логику можно было тестировать без живого aiogram.
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.schemas import ChatMessage

START_SYSTEM_PROMPT = (
    "Ты — тьютор по курсу машинного обучения (mlcourse.ai). "
    "Отвечай кратко, по-дружески и по делу."
)


async def build_start_reply(client: LLMClient, model: str, user_text: str) -> str:
    """Логика ответа на /start: вызов модели + подпись с именем модели."""
    messages = [
        ChatMessage(role="system", content=START_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_text or "/start"),
    ]
    answer = await client.chat(messages, model=model)
    return f"[модель: {model}]\n\n{answer}"


def make_router(client: LLMClient, model: str) -> Router:
    """Собирает роутер с внедрёнными зависимостями (client, model)."""
    router = Router()

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        reply = await build_start_reply(client, model, message.text or "")
        await message.answer(reply)

    return router
