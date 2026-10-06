"""Точка входа бота: собирает зависимости и запускает polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from llm_tutor.bot.handlers import make_router
from llm_tutor.config import get_settings
from llm_tutor.course.seed import load_seed
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.llm.client import LLMClient


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    conn = get_conn(settings.db_path)
    migrate(conn)
    load_seed(conn)  # граф темы — идемпотентный upsert seed-файла

    client = LLMClient(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        default_model=settings.tutor_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=settings.llm_max_retries,
    )

    try:
        bot = Bot(token=settings.telegram_bot_token.get_secret_value())
        dispatcher = Dispatcher()
        dispatcher.include_router(
            make_router(
                conn,
                client,
                settings.tutor_model,
                rag_top_k=settings.context_rag_top_k,
                dialog_tail=settings.context_dialog_tail,
            )
        )
        await dispatcher.start_polling(bot)
    finally:
        await client.aclose()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
