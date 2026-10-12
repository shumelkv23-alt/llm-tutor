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


# --- ключ OpenRouter ---

KEY_URL = f"{BASE_URL}/key"


@respx.mock
async def test_http_error_carries_status() -> None:
    """Код ответа провайдера доступен вызывающему: 401 — это ключ, а не сеть."""
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "User not found."}})
    )
    with pytest.raises(LLMError) as caught:
        await _client().chat([ChatMessage(role="user", content="hi")])
    assert caught.value.status == 401
    assert not caught.value.retryable


@respx.mock
async def test_network_error_has_no_status() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("нет сети"))
    with pytest.raises(LLMError) as caught:
        await _client(max_retries=0).chat([ChatMessage(role="user", content="hi")])
    assert caught.value.status is None


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_key_problem_reports_rejected_key(status: int) -> None:
    route = respx.get(KEY_URL).mock(
        return_value=httpx.Response(status, json={"error": {"message": "User not found."}})
    )
    problem = await _client().key_problem()
    assert problem is not None and str(status) in problem and "User not found" in problem
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-test"


@respx.mock
async def test_key_problem_accepts_valid_key() -> None:
    respx.get(KEY_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"label": "x", "is_management_key": False, "is_provisioning_key": False}}
        )
    )
    assert await _client().key_problem() is None


@respx.mock
@pytest.mark.parametrize("flag", ["is_management_key", "is_provisioning_key"])
async def test_key_problem_reports_management_key(flag: str) -> None:
    """Ключ управления действителен (/key отвечает 200), но модели им не вызвать."""
    respx.get(KEY_URL).mock(
        return_value=httpx.Response(200, json={"data": {"label": "sk-or-v1-…", flag: True}})
    )
    problem = await _client().key_problem()
    assert problem is not None and "ключ управления" in problem


async def test_key_problem_reports_non_ascii_key() -> None:
    """Невидимый символ после копирования ключа — проблема ключа, а не тихий сбой.

    httpx не соберёт такой заголовок (UnicodeEncodeError), и без этой проверки
    фоновая проверка при старте умирала бы молча.
    """
    problem = await _client(api_key="sk-or-v1-abc​").key_problem()
    assert problem is not None and "не-ASCII" in problem
    assert "​" not in problem and "sk-or-v1-abc" not in problem


@respx.mock
async def test_provider_echo_does_not_leak_key() -> None:
    """Если прокси вернёт заголовок в теле ответа, ключа в тексте ошибки нет."""
    echo = "got header Bearer sk-test"
    respx.post(CHAT_URL).mock(return_value=httpx.Response(401, text=echo))
    respx.get(KEY_URL).mock(return_value=httpx.Response(401, text=echo))

    with pytest.raises(LLMError) as caught:
        await _client().chat([ChatMessage(role="user", content="hi")])
    problem = await _client().key_problem()

    assert "sk-test" not in str(caught.value)
    assert problem is not None and "sk-test" not in problem


@respx.mock
async def test_key_problem_ignores_odd_body() -> None:
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, text="не json"))
    assert await _client().key_problem() is None
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=[1, 2]))
    assert await _client().key_problem() is None


@respx.mock
async def test_key_problem_ignores_network_and_provider_failures() -> None:
    """Сеть и 5xx — не вина ключа: о них скажет первый ход, а не старт."""
    respx.get(KEY_URL).mock(side_effect=httpx.ConnectError("нет сети"))
    assert await _client().key_problem() is None
    respx.get(KEY_URL).mock(return_value=httpx.Response(503, text="busy"))
    assert await _client().key_problem() is None
