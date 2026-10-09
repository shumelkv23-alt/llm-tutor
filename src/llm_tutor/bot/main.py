"""Точка входа бота: собирает зависимости и запускает polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from llm_tutor.bot.diagnostic import make_diagnostic_router
from llm_tutor.bot.handlers import make_router
from llm_tutor.bot.serial import SerialMiddleware
from llm_tutor.bot.survey import make_survey_router
from llm_tutor.bot.themes import make_themes_router
from llm_tutor.config import get_settings
from llm_tutor.course.seed import items_without_rubric, load_course, nodes_without_items
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.llm.client import LLMClient


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    conn = get_conn(settings.db_path)
    client = LLMClient(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        default_model=settings.tutor_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=settings.llm_max_retries,
    )

    try:
        migrate(conn)
        course = load_course(conn)  # все модули курса — один проход replace_seed
        for warning in course.warnings:
            logging.warning(warning)
        missing = nodes_without_items(course)
        if missing:
            logging.warning(
                "Узлы без заданий — диагностика их не проверит: %s", ", ".join(missing)
            )
        without_rubric = items_without_rubric(course)
        if without_rubric:
            logging.warning(
                "Открытые задания без рубрики — проверить нечем, выдаваться не будут: %s",
                without_rubric,
            )
        bot = Bot(token=settings.telegram_bot_token.get_secret_value())
        dispatcher = Dispatcher()
        # Ходы по одному: второе сообщение во время хода ждёт его конца.
        dispatcher.update.outer_middleware(SerialMiddleware())
        # FSM-потоки (анкета, диагностика) идут первыми: их шаги не должны
        # попадать в тьютор-путь.
        dispatcher.include_router(
            make_survey_router(conn, settings, client, settings.tutor_model)
        )
        dispatcher.include_router(make_diagnostic_router(conn, settings))
        dispatcher.include_router(make_themes_router(conn, settings))
        dispatcher.include_router(
            make_router(conn, client, settings.tutor_model, settings=settings)
        )
        await dispatcher.start_polling(bot)
    finally:
        await client.aclose()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
