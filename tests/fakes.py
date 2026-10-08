"""Общие подставные объекты для тестов."""

import asyncio
import itertools
from types import SimpleNamespace

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup

from llm_tutor.db import repos
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict


class NullSession(AiohttpSession):
    """Сессия-заглушка: наружу ничего не уходит.

    Нужна тестам, которые прогоняют событие через настоящий ``Dispatcher``:
    ответы хендлеров улетают в никуда, а фильтры и роутеры работают как в бою.
    """

    async def __call__(self, bot, method, timeout=None):
        return True


class FakeMessage:
    """Подставное сообщение: помнит всё, что бот в него отправил или поправил.

    ``answer`` возвращает новое подставное сообщение — как настоящий
    ``Message.answer``: у отправленного свой ``message_id``, по нему анкета
    привязывает нажатия. ``log`` — порядок действий (``answer`` / ``edit`` /
    ``callback``), чтобы проверять, что «часики» гаснут раньше правки.
    """

    _ids = itertools.count(1)

    def __init__(self, text: str = "", *, log: list[str] | None = None) -> None:
        self.text = text
        self.message_id = next(FakeMessage._ids)
        self.sent: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.edits: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.reply_markup = None
        self.log: list[str] = [] if log is None else log
        self.bot = None
        self.chat = SimpleNamespace(id=1)

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "FakeMessage":
        self.sent.append((text, reply_markup))
        self.log.append("answer")
        sent = FakeMessage(text, log=self.log)
        sent.reply_markup = reply_markup
        return sent

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> "FakeMessage":
        self.edits.append((text, reply_markup))
        self.log.append("edit")
        self.text = text
        self.reply_markup = reply_markup
        return self

    async def edit_reply_markup(self, reply_markup=None, **kwargs) -> "FakeMessage":
        self.reply_markup = reply_markup
        return self

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]


class FakeCallback:
    """Подставное нажатие инлайн-кнопки."""

    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answered = False
        self.answer_text: str | None = None

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs) -> None:
        # Уступаем цикл, как настоящий сетевой вызов: тесты гонок проверяют,
        # что решение по нажатию принято ДО него.
        await asyncio.sleep(0)
        self.answered = True
        self.answer_text = text
        self.message.log.append("callback")


def _fsm() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )


def _named(router, kind: str, name: str):
    """Находит хендлер по имени функции — не зависит от порядка регистрации."""
    for handler in getattr(router, kind).handlers:
        if handler.callback.__name__ == name:
            return handler.callback
    raise AssertionError(f"нет хендлера {name}")


class GradingTutor:
    """Отвечает и как тьютор, и как грейдер: вердикт задаётся параметром."""

    def __init__(self, conn, *, passed: bool) -> None:
        self.conn = conn
        self.passed = passed

    def _verdict(self) -> RubricVerdict:
        criteria = [
            CriterionVerdict(
                id=criterion.id,
                passed=self.passed,
                quote="groupby" if self.passed else None,
            )
            for rubric in repos.get_rubrics(self.conn)
            for criterion in repos.get_criteria(self.conn, rubric.id)
        ]
        return RubricVerdict(criteria=criteria)

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        if schema is RubricVerdict:
            return self._verdict()
        return schema(reply="Разбираем.", hint_level=0)

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        return "Разбираем."
