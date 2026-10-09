"""Апдейты по одному: ход диалога не должен читать состояние, которое меняет другой.

Апдейты идут задачами (``handle_as_tasks``), и второе сообщение во время хода
(ход ждёт модель секундами) читало то же состояние сессии: дубль проверки,
дубль «🎉» (аудит среза 25, 25-L4). Ученик на БД один, поэтому один замок на
весь бот: следующий апдейт ждёт, пока закончится текущий.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class SerialMiddleware(BaseMiddleware):
    """Внешний middleware диспетчера: апдейты обрабатываются строго по очереди."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._lock:
            return await handler(event, data)
