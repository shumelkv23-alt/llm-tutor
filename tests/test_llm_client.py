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
        backoff_base=0.0,  # без реальных задержек в тестах
    )
    defaults.update(kwargs)
    return LLMClient(**defaults)


def _ok_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


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


# --- клиент: happy path ---


class _Grade(BaseModel):
    score: float


@respx.mock
async def test_chat_returns_content() -> None:
    respx.post(CHAT_URL).mock(return_value=_ok_response("привет"))
    result = await _client().chat([ChatMessage(role="user", content="hi")])
    assert result == "привет"


@respx.mock
async def test_chat_uses_explicit_model_in_payload() -> None:
    """Абстракция модели: явный model уходит в тело запроса."""
    route = respx.post(CHAT_URL).mock(return_value=_ok_response("ok"))
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
        _ok_response("не json"),
        _ok_response('{"score": 0.9}'),
    ]
    result = await _client().chat_structured([ChatMessage(role="user", content="x")], _Grade)
    assert result == _Grade(score=0.9)
    assert route.call_count == 2


# --- клиент: ошибки и ретраи ---


@respx.mock
async def test_chat_retries_transient_http_error() -> None:
    """500 (временный сбой) повторяется, затем успех."""
    route = respx.post(CHAT_URL)
    route.side_effect = [
        httpx.Response(500, text="boom"),
        _ok_response("ok"),
    ]
    result = await _client().chat([ChatMessage(role="user", content="hi")])
    assert result == "ok"
    assert route.call_count == 2


@respx.mock
async def test_chat_http_error_exhausts_retries() -> None:
    """Постоянный 500 — ретраи исчерпываются и падает LLMError."""
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(LLMError, match="500"):
        await _client().chat([ChatMessage(role="user", content="hi")])
    assert route.call_count == 2  # max_retries(1) + 1


@respx.mock
async def test_chat_non_retryable_4xx_raises_immediately() -> None:
    """400 — не ретраится, падает с первой попытки."""
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(400, text="bad"))
    with pytest.raises(LLMError, match="400"):
        await _client().chat([ChatMessage(role="user", content="hi")])
    assert route.call_count == 1


@respx.mock
async def test_chat_network_error_raises() -> None:
    """Сетевая ошибка — retryable, после ретраев падает LLMError."""
    route = respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(LLMError):
        await _client().chat([ChatMessage(role="user", content="hi")])
    assert route.call_count == 2


@respx.mock
async def test_chat_non_json_response_raises() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(LLMError, match="не-JSON"):
        await _client().chat([ChatMessage(role="user", content="hi")])


@respx.mock
async def test_chat_malformed_structure_raises() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(LLMError, match="структура"):
        await _client().chat([ChatMessage(role="user", content="hi")])


@respx.mock
async def test_chat_empty_content_raises() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
    )
    with pytest.raises(LLMError, match="пустой content"):
        await _client().chat([ChatMessage(role="user", content="hi")])


@respx.mock
async def test_chat_structured_all_invalid_raises() -> None:
    """Все попытки невалидны — клиент падает после max_retries + 1."""
    route = respx.post(CHAT_URL).mock(return_value=_ok_response("не json"))
    with pytest.raises(LLMError, match="валидный ответ"):
        await _client().chat_structured([ChatMessage(role="user", content="x")], _Grade)
    assert route.call_count == 2  # max_retries(1) + 1
