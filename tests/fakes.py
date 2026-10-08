"""Общие подставные объекты для тестов."""

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
    """Подставное сообщение: помнит всё, что бот в него отправил."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.sent: list[tuple[str, InlineKeyboardMarkup | None]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> None:
        self.sent.append((text, reply_markup))

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]


class FakeCallback:
    """Подставное нажатие инлайн-кнопки."""

    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answered = False

    async def answer(self, *args, **kwargs) -> None:
        self.answered = True


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
