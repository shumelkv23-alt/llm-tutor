"""HTTP-клиент к OpenRouter Chat Completions API.

Абстракция модели: модель — параметр вызова, дефолт — из конфига.
Смена модели = правка `.env`, код не трогается.
"""

import asyncio
from typing import Sequence, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from llm_tutor.llm.schemas import ChatMessage

T = TypeVar("T", bound=BaseModel)

# Статусы, которые имеет смысл повторять: лимиты и временные сбои провайдера.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Ошибка обращения к LLM-провайдеру (сеть, HTTP, парсинг).

    ``retryable`` помечает ошибки, которые имеет смысл повторить
    (сеть, лимиты, временные сбои провайдера).
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient:
    """Тонкий асинхронный клиент к OpenRouter."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        max_retries: int = 1,
        timeout: float = 60.0,
        backoff_base: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def aclose(self) -> None:
        """Закрыть общий HTTP-клиент (на shutdown приложения)."""
        await self._http.aclose()

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Обычный текстовый ответ модели (с ретраем на временные сбои)."""
        payload = self._build_payload(messages, model, temperature)
        return await self._request(payload)

    async def chat_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: Type[T],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> T:
        """Ответ в JSON, провалидированный pydantic-схемой, с ретраем."""
        payload = self._build_payload(messages, model, temperature, json_mode=True)
        return await self._request(payload, schema=schema)

    # --- внутреннее ---

    def _build_payload(
        self,
        messages: Sequence[ChatMessage],
        model: str | None,
        temperature: float | None,
        *,
        json_mode: bool = False,
    ) -> dict:
        payload: dict = {
            "model": model or self._default_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": self._max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _request(self, payload: dict, *, schema: Type[T] | None = None) -> str | T:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self._backoff_base * (2 ** (attempt - 1)))

            try:
                data = await self._post(payload)
            except LLMError as exc:
                last_error = exc
                if exc.retryable and attempt < self._max_retries:
                    continue
                raise

            content = self._extract_content(data)
            if schema is None:
                return content

            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = exc
                # JSON не соответствует схеме — пробуем ещё раз (если есть попытки).

        raise LLMError(
            f"LLM не вернул валидный ответ за {self._max_retries + 1} попыток: {last_error}"
        )

    async def _post(self, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._http.post("/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(
                f"Сетевая ошибка при обращении к OpenRouter: {exc}", retryable=True
            ) from exc

        if response.status_code >= 400:
            raise LLMError(
                f"OpenRouter вернул HTTP {response.status_code}: {response.text[:200]}",
                retryable=response.status_code in _RETRYABLE_STATUS,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError("OpenRouter вернул не-JSON ответ") from exc

    @staticmethod
    def _extract_content(data: dict) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Неожиданная структура ответа OpenRouter: {data!r}") from exc
        if content is None:
            raise LLMError("OpenRouter вернул пустой content")
        return content
