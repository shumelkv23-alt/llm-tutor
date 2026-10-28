"""Распознавание речи: серверный запасной путь, когда браузер не умеет.

Основной путь — Web Speech API в браузере: запись не уходит с устройства и
распознаётся по ходу речи. Firefox и часть мобильных браузеров его не
поддерживают — там та же запись уходит сюда, в OpenRouter
(``/audio/transcriptions``, модель Whisper). Модель и адрес — из настроек, как
у остальных клиентов: смена модели = правка `.env`.
"""

import base64
import logging

import httpx

logger = logging.getLogger(__name__)

# Форматы, которые принимает OpenRouter в ``input_audio.format``. Браузер
# пишет webm/ogg (Chrome, Firefox) или mp4 (Safari); wav/mp3 — запас на
# будущие дорожки и тесты.
STT_FORMATS = frozenset({"wav", "mp3", "flac", "m4a", "ogg", "webm", "aac"})

# Ключ не принят: сообщение должно назвать причину, а не повторять запрос.
_AUTH_STATUS = {401, 403}


class STTError(RuntimeError):
    """Распознавание не удалось: сеть, отказ провайдера, пустой ответ.

    ``status`` — HTTP-код ответа провайдера, если он был (``None`` — сеть).
    Текст ошибки показывается ученику, поэтому ключ и секреты в него не
    попадают.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SpeechToText:
    """Тонкий асинхронный клиент распознавания речи OpenRouter."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        language: str = "ru",
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._language = language
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def aclose(self) -> None:
        """Закрыть общий HTTP-клиент (на shutdown приложения)."""
        await self._http.aclose()

    async def transcribe(
        self, audio: bytes, *, audio_format: str, language: str | None = None
    ) -> str:
        """Расшифровка записи в текст.

        Пустая расшифровка — не успех: ученику она ничего не даёт, а пустое
        поле ответа выглядело бы как его промах.
        """
        if audio_format not in STT_FORMATS:
            raise STTError(f"Формат записи «{audio_format}» не поддерживается.")
        payload = {
            "model": self._model,
            "input_audio": {
                "data": base64.b64encode(audio).decode("ascii"),
                "format": audio_format,
            },
            "language": (language or self._language).strip() or None,
        }
        response = await self._post(payload)
        text = (response.get("text") or "").strip() if isinstance(response, dict) else ""
        if not text:
            raise STTError("Речь не распознана: попробуйте записать ответ ещё раз.")
        return text

    async def _post(self, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._http.post(
                "/audio/transcriptions", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise STTError(f"Сетевая ошибка при распознавании речи: {exc}") from exc
        text = self._redact(response.text)
        if response.status_code >= 400:
            if response.status_code in _AUTH_STATUS:
                raise STTError(
                    f"OpenRouter не принял ключ для распознавания речи "
                    f"(HTTP {response.status_code}): {text[:200]}",
                    status=response.status_code,
                )
            raise STTError(
                f"Распознавание речи недоступно (HTTP {response.status_code}): {text[:200]}",
                status=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise STTError("OpenRouter вернул не-JSON ответ на распознавание речи.") from exc
        return data if isinstance(data, dict) else {}

    def _redact(self, text: str) -> str:
        """Текст ответа провайдера — без ключа (прокси может вернуть заголовок эхом)."""
        return text.replace(self._api_key, "***") if self._api_key else text
