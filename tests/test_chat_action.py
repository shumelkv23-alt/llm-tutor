"""«Печатает…», пока бот ждёт модель."""

import asyncio

from fakes import FakeMessage, _fsm, _named

from llm_tutor.bot.chat_action import typing_action
from llm_tutor.bot.handlers import make_router
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.student import survey


class _RecordingBot:
    """Бот-заглушка: помнит отправленные chat action."""

    id = 42

    def __init__(self) -> None:
        self.actions: list[tuple[int, str]] = []
        # Ждём событие, а не таймер: шаг таймера Windows (~15 мс) делал
        # ожидание по времени нестабильным.
        self.sent = asyncio.Event()

    async def send_chat_action(self, chat_id, action, message_thread_id=None, **kwargs):
        self.actions.append((chat_id, action))
        self.sent.set()
        return True


class _SlowTutor:
    """Тьютор, который «думает», пока в чате не появится «печатает…»."""

    def __init__(self, bot: _RecordingBot) -> None:
        self.bot = bot

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        await asyncio.wait_for(self.bot.sent.wait(), timeout=1.0)
        return schema(reply="ок", hint_level=0)


async def test_typing_action_sends_typing_while_waiting() -> None:
    message = FakeMessage()
    message.bot = _RecordingBot()

    async with typing_action(message):
        await asyncio.wait_for(message.bot.sent.wait(), timeout=1.0)

    assert message.bot.actions == [(1, "typing")]


async def test_typing_action_is_noop_without_bot() -> None:
    async with typing_action(FakeMessage()):
        pass


async def test_free_text_shows_typing(conn, settings) -> None:
    load_seed(conn)
    for block in survey.BLOCKS:
        repos.set_fact(conn, block.key, survey.SELF_LEVELS[1], source="self")
    bot = _RecordingBot()
    router = make_router(conn, _SlowTutor(bot), "m", settings=settings)
    message = FakeMessage("как работает groupby?")
    message.bot = bot

    await _named(router, "message", "on_text")(message, _fsm())

    assert (1, "typing") in message.bot.actions
