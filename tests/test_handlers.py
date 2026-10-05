"""Тесты хендлеров бота (с замоканным LLM-клиентом)."""

from aiogram import Router

from llm_tutor.bot.handlers import build_start_reply, make_router
from llm_tutor.llm.client import LLMError
from llm_tutor.llm.schemas import ChatMessage


class _FakeClient:
    """Подставной клиент: запоминает вызовы и возвращает заготовленный ответ."""

    def __init__(self, answer: str = "Привет!") -> None:
        self.answer = answer
        self.calls: list[tuple[list[ChatMessage], str]] = []

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        self.calls.append((list(messages), model))
        return self.answer


class _FailingClient:
    async def chat(self, messages, *, model=None, temperature=None) -> str:
        raise LLMError("сбой", retryable=False)


async def test_build_start_reply_contains_model_name() -> None:
    """Ответ /start подписан именем модели (видно абстракцию модели)."""
    client = _FakeClient("Привет!")
    reply = await build_start_reply(client, "anthropic/claude-sonnet-4.5", "hi")

    assert "anthropic/claude-sonnet-4.5" in reply
    assert "Привет!" in reply


async def test_build_start_reply_builds_system_and_user_messages() -> None:
    """Клиент получает system-промпт и текст ученика."""
    client = _FakeClient()
    await build_start_reply(client, "m", "учу pandas")

    messages, model = client.calls[0]
    assert model == "m"
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[1].content == "учу pandas"


async def test_build_start_reply_catches_llm_error() -> None:
    """Сбой LLM не роняет бота — ученик получает понятный ответ."""
    reply = await build_start_reply(_FailingClient(), "m", "hi")
    assert "не смог получить ответ" in reply.lower()


async def test_build_start_reply_truncates_long_answer() -> None:
    """Ответ длиннее лимита Telegram режется до безопасной длины."""
    client = _FakeClient("а" * 5000)
    reply = await build_start_reply(client, "m", "hi")
    assert len(reply) <= 4001  # 4000 + многоточие
    assert reply.endswith("…")


def test_make_router_registers_start_handler() -> None:
    """make_router возвращает роутер с зарегистрированным /start."""
    router = make_router(_FakeClient(), "m")
    assert isinstance(router, Router)
    assert router.message.handlers
