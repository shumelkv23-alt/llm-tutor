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
    (сеть, лимиты, временные сбои провайдера). ``status`` — HTTP-код ответа
    провайдера, если ответ был (``None`` — сеть или разбор).
    """

    def __init__(
        self, message: str, *, retryable: bool = False, status: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


# Ключ не принят: повтор не поможет, нужен новый ключ в .env.
_AUTH_STATUS = {401, 403}


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

    async def key_problem(self) -> str | None:
        """Что не так с ключом OpenRouter (``None`` — ключ принят или проверить нельзя).

        Запрос ``GET /key`` бесплатный и не тратит токены. Сеть и сбои
        провайдера проблемой ключа не считаются: о них скажет первый ход.
        """
        if not self._api_key.isascii():
            # Такой заголовок httpx не соберёт (UnicodeEncodeError) — ни здесь,
            # ни в ходе тьютора. Сам символ в текст не кладём: он невидим.
            return (
                "В OPENROUTER_API_KEY есть не-ASCII символ — обычно невидимый "
                "пробел, прилипший при копировании ключа"
            )
        try:
            response = await self._http.get(
                "/key", headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.HTTPError:
            return None
        if response.status_code in _AUTH_STATUS:
            return (
                f"OpenRouter не принял ключ (HTTP {response.status_code}): "
                f"{self._redact(response.text)[:200]}"
            )
        if response.status_code != 200:
            return None
        try:
            data = response.json().get("data") or {}
        except (ValueError, AttributeError):
            return None
        # Ключ управления (Provisioning / Management key) создаёт и гасит другие
        # ключи, а модели им вызывать нельзя: /chat/completions отвечает 401
        # «User not found», хотя сам ключ действителен.
        if isinstance(data, dict) and (
            data.get("is_management_key") or data.get("is_provisioning_key")
        ):
            return (
                "В OPENROUTER_API_KEY лежит ключ управления (Provisioning / Management "
                "key): им нельзя вызывать модели, OpenRouter отвечает 401 «User not found»"
            )
        return None

    # --- внутреннее ---

    def _redact(self, text: str) -> str:
        """Текст ответа провайдера — в лог, но без ключа (прокси может вернуть заголовок эхом)."""
        return text.replace(self._api_key, "***") if self._api_key else text

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
                f"OpenRouter вернул HTTP {response.status_code}: "
                f"{self._redact(response.text)[:200]}",
                retryable=response.status_code in _RETRYABLE_STATUS,
                status=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError("OpenRouter вернул не-JSON ответ") from exc

    @staticmethod
    def _extract_content(data: dict) -> str:
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Неожиданная структура ответа OpenRouter: {data!r}") from exc
        if not content:
            # Причина остановки объясняет пустоту: «length» — модель израсходовала
            # лимит вывода (у reasoning-моделей на размышления), «content_filter» —
            # ответ вырезан провайдером.
            reason = choice.get("finish_reason") or "неизвестна"
            usage = data.get("usage") or {}
            tokens = usage.get("completion_tokens")
            raise LLMError(
                f"OpenRouter вернул пустой content (причина остановки: {reason}"
                + (f", потрачено токенов: {tokens}" if tokens else "")
                + "). Проверь GENERATION_MAX_TOKENS/LLM_MAX_TOKENS: у моделей с "
                "размышлениями лимит уходит на них"
            )
        return content
