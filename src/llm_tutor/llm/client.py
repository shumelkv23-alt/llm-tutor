"""HTTP-клиент к OpenRouter Chat Completions API.

Абстракция модели: модель — параметр вызова, дефолт — из конфига.
Смена модели = правка `.env`, код не трогается.
"""

from typing import Sequence, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from llm_tutor.llm.schemas import ChatMessage

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Ошибка обращения к LLM-провайдеру (сеть, HTTP, парсинг)."""


class LLMClient:
    """Тонкий асинхронный клиент к OpenRouter."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        temperature: float = 0.4,
        max_retries: int = 1,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._temperature = temperature
        self._max_retries = max_retries
        self._timeout = timeout

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Обычный текстовый ответ модели."""
        payload = self._build_payload(messages, model, temperature)
        data = await self._post(payload)
        return self._extract_content(data)

    async def chat_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: Type[T],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> T:
        """Ответ в JSON, провалидированный pydantic-схемой, с ретраем на провал."""
        payload = self._build_payload(messages, model, temperature, json_mode=True)
        last_error: ValidationError | None = None
        for _ in range(self._max_retries + 1):
            data = await self._post(payload)
            content = self._extract_content(data)
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:  # JSON не соответствует схеме — пробуем ещё раз
                last_error = exc
        raise LLMError(
            f"LLM не вернул валидный JSON за {self._max_retries + 1} попыток: {last_error}"
        )

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
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _post(self, payload: dict) -> dict:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"Сетевая ошибка при обращении к OpenRouter: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(
                f"OpenRouter вернул HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError("OpenRouter вернул не-JSON ответ") from exc

    @staticmethod
    def _extract_content(data: dict) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Неожиданная структура ответа OpenRouter: {data!r}") from exc
