"""Тесты схем сообщений и LLM-клиента."""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel, ValidationError

from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.schemas import ChatMessage

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"


def _client(**kwargs) -> LLMClient:
    defaults = dict(
        base_url=BASE_URL,
        api_key="sk-test",
        default_model="anthropic/claude-sonnet-4.5",
    )
    defaults.update(kwargs)
    return LLMClient(**defaults)


# --- схемы ---


def test_chat_message_roundtrip() -> None:
    """Сообщение сериализуется в формат OpenRouter и обратно."""
    msg = ChatMessage(role="user", content="привет")
    assert msg.model_dump() == {"role": "user", "content": "привет"}
    assert ChatMessage.model_validate({"role": "user", "content": "привет"}) == msg


def test_chat_message_unknown_role_rejected() -> None:
    """Неизвестная роль отклоняется на этапе валидации."""
    with pytest.raises(ValidationError):
        ChatMessage(role="bot", content="x")


# --- клиент ---


class _Grade(BaseModel):
    score: float


@respx.mock
async def test_chat_returns_content() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "привет"}}]})
    )
    result = await _client().chat([ChatMessage(role="user", content="hi")])
    assert result == "привет"


@respx.mock
async def test_chat_http_error_raises() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="internal error"))
    with pytest.raises(LLMError, match="500"):
        await _client().chat([ChatMessage(role="user", content="hi")])


@respx.mock
async def test_chat_uses_explicit_model_in_payload() -> None:
    """Абстракция модели: явный model уходит в тело запроса."""
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    await _client().chat(
        [ChatMessage(role="user", content="hi")], model="anthropic/claude-haiku-4.5"
    )
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "anthropic/claude-haiku-4.5"


@respx.mock
async def test_chat_structured_retries_invalid_json() -> None:
    """Первый ответ — не JSON, второй валиден: клиент ретраит и возвращает модель."""
    route = respx.post(CHAT_URL)
    route.side_effect = [
        httpx.Response(200, json={"choices": [{"message": {"content": "не json"}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": '{"score": 0.9}'}}]}),
    ]
    result = await _client().chat_structured([ChatMessage(role="user", content="x")], _Grade)
    assert result == _Grade(score=0.9)
    assert route.call_count == 2


@respx.mock
async def test_chat_structured_all_invalid_raises() -> None:
    """Все попытки невалидны — клиент падает после max_retries + 1."""
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "не json"}}]})
    )
    with pytest.raises(LLMError, match="валидный JSON"):
        await _client().chat_structured([ChatMessage(role="user", content="x")], _Grade)
    assert route.call_count == 2  # max_retries(1) + 1
