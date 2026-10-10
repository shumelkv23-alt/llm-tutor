"""Запуск веб-приложения: ``uv run python -m llm_tutor.web``."""

import logging

import uvicorn

from llm_tutor.config import get_settings
from llm_tutor.web.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    main()
