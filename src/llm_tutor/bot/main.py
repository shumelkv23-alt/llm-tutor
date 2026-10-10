"""Точка входа бота: собирает зависимости и запускает polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from llm_tutor.bot.diagnostic import make_diagnostic_router
from llm_tutor.bot.handlers import make_router
from llm_tutor.bot.survey import make_survey_router
from llm_tutor.bot.themes import make_themes_router
from llm_tutor.config import Settings, get_settings
from llm_tutor.course.seed import (
    items_without_rubric,
    load_seed,
    nodes_without_items,
    nodes_without_theory,
)
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.llm.client import LLMClient


def require_telegram_token(settings: Settings) -> str:
    """Токен бота или ясная ошибка: в настройках он необязателен — нужен только боту."""
    if settings.telegram_bot_token is None:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN пуст. Заполни его в .env (см. .env.example)"
            " или запусти веб: uv run python -m llm_tutor.web"
        )
    return settings.telegram_bot_token.get_secret_value()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    token = require_telegram_token(settings)

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
        seed = load_seed(conn)  # граф темы — идемпотентный upsert seed-файла
        missing = nodes_without_items(seed)
        if missing:
            logging.warning(
                "Узлы без заданий — диагностика их не проверит: %s", ", ".join(missing)
            )
        no_theory = nodes_without_theory(seed)
        if no_theory:
            logging.warning("Узлы без конспекта: %s", ", ".join(no_theory))
        without_rubric = items_without_rubric(seed)
        if without_rubric:
            logging.warning(
                "Открытые задания без рубрики — проверить нечем, выдаваться не будут: %s",
                without_rubric,
            )
        bot = Bot(token=token)
        dispatcher = Dispatcher()
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
