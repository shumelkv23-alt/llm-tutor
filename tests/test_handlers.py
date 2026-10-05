"""Тесты хендлеров бота (с замоканным LLM-клиентом)."""

from aiogram import Router

from llm_tutor.bot.handlers import build_start_reply, make_router
from llm_tutor.llm.schemas import ChatMessage


class _FakeClient:
    """Подставной клиент: запоминает вызовы и возвращает заготовленный ответ."""

    def __init__(self, answer: str = "Привет!") -> None:
        self.answer = answer
        self.calls: list[tuple[list[ChatMessage], str]] = []

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        self.calls.append((list(messages), model))
        return self.answer


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


def test_make_router_registers_start_handler() -> None:
    """make_router возвращает роутер с зарегистрированным /start."""
    router = make_router(_FakeClient(), "m")
    assert isinstance(router, Router)
    assert router.message.handlers
