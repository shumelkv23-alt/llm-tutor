"""Распознавание речи: клиент OpenRouter и маршрут ``/api/lesson/transcribe``.

Основной путь распознавания — браузерный (Web Speech API); сервер нужен
Firefox и части мобильных браузеров. Здесь проверяется серверный путь:
форма запроса к провайдеру, отказы и лимит расшифровок на ученика.
"""

import base64
import json

import httpx
import pytest

from llm_tutor.llm.stt import STTError, SpeechToText
from llm_tutor.web.app import create_app

ANN = {"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"}
AUDIO = b"\x1aE\xdf\xa3fake-webm-bytes" * 4
BASE64_AUDIO = base64.b64encode(AUDIO).decode("ascii")


def _client(handler) -> SpeechToText:
    """Клиент с подставной сетью: httpx.MockTransport вместо OpenRouter."""
    speech = SpeechToText(
        base_url="https://openrouter.test/api/v1",
        api_key="secret-key",
        model="openai/whisper-large-v3",
    )
    speech._http = httpx.AsyncClient(
        base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler)
    )
    return speech


async def test_transcription_payload_and_text() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"text": "  Список изменяемый.  "})

    speech = _client(handler)
    try:
        text = await speech.transcribe(AUDIO, audio_format="webm")
    finally:
        await speech.aclose()

    assert text == "Список изменяемый."
    assert seen["path"].endswith("/audio/transcriptions")
    assert seen["auth"] == "Bearer secret-key"
    body = seen["body"]
    assert body["model"] == "openai/whisper-large-v3"
    assert body["input_audio"]["format"] == "webm"
    assert base64.b64decode(body["input_audio"]["data"]) == AUDIO
    assert body["language"] == "ru"


async def test_transcription_language_can_be_overridden() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"text": "ok"})

    speech = _client(handler)
    try:
        await speech.transcribe(AUDIO, audio_format="wav", language="en")
    finally:
        await speech.aclose()

    assert seen["language"] == "en"


async def test_unknown_format_is_rejected_without_request() -> None:
    called = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover — не должен зваться
        called.append(request)
        return httpx.Response(200, json={"text": "ok"})

    speech = _client(handler)
    try:
        with pytest.raises(STTError, match="не поддерживается"):
            await speech.transcribe(AUDIO, audio_format="midi")
    finally:
        await speech.aclose()

    assert called == []


async def test_rejected_key_is_named_in_the_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Missing Authentication header")

    speech = _client(handler)
    try:
        with pytest.raises(STTError) as error:
            await speech.transcribe(AUDIO, audio_format="webm")
    finally:
        await speech.aclose()

    assert error.value.status == 401
    assert "не принял ключ" in str(error.value)


async def test_provider_failure_and_empty_text_are_errors() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    speech = _client(failing)
    try:
        with pytest.raises(STTError, match="HTTP 500"):
            await speech.transcribe(AUDIO, audio_format="webm")
    finally:
        await speech.aclose()

    def silent(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "   "})

    speech = _client(silent)
    try:
        with pytest.raises(STTError, match="не распознана"):
            await speech.transcribe(AUDIO, audio_format="webm")
    finally:
        await speech.aclose()


async def test_network_failure_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    speech = _client(handler)
    try:
        with pytest.raises(STTError, match="Сетевая ошибка"):
            await speech.transcribe(AUDIO, audio_format="webm")
    finally:
        await speech.aclose()


# --- маршрут ---


class FakeSpeech:
    """Подставное распознавание: копит записи, отвечает заготовкой."""

    def __init__(self, text: str = "Список изменяемый, а кортеж — нет.", error: STTError | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[bytes, str, str | None]] = []
        self.closed = False

    async def transcribe(self, audio: bytes, *, audio_format: str, language: str | None = None) -> str:
        self.calls.append((audio, audio_format, language))
        if self.error is not None:
            raise self.error
        return self.text

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def speech_app(web_settings, web_llm, web_mailer):
    speech = FakeSpeech()
    app = create_app(web_settings, client=web_llm, mailer=web_mailer, stt=speech)
    yield app, speech
    app.state.accounts.close()


@pytest.fixture
async def speech_web(speech_app):
    app, speech = speech_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, speech


async def test_transcribe_returns_recognized_text(speech_web) -> None:
    web, speech = speech_web
    assert (await web.post("/api/auth/register", json=ANN)).status_code == 201

    response = await web.post(
        "/api/lesson/transcribe", json={"audio": BASE64_AUDIO, "format": "webm"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"text": "Список изменяемый, а кортеж — нет."}
    assert speech.calls == [(AUDIO, "webm", None)]


async def test_transcribe_needs_login(speech_web) -> None:
    web, speech = speech_web

    response = await web.post(
        "/api/lesson/transcribe", json={"audio": BASE64_AUDIO, "format": "webm"}
    )

    assert response.status_code == 401
    assert speech.calls == []


async def test_transcribe_rejects_broken_base64(speech_web) -> None:
    web, speech = speech_web
    assert (await web.post("/api/auth/register", json=ANN)).status_code == 201

    response = await web.post(
        "/api/lesson/transcribe", json={"audio": "это не base64 %%%", "format": "webm"}
    )

    assert response.status_code == 422
    assert speech.calls == []


async def test_transcribe_rejects_unknown_format(speech_web) -> None:
    web, speech = speech_web
    assert (await web.post("/api/auth/register", json=ANN)).status_code == 201

    response = await web.post(
        "/api/lesson/transcribe", json={"audio": BASE64_AUDIO, "format": "midi"}
    )

    assert response.status_code == 422
    assert speech.calls == []


async def test_transcribe_rejects_too_long_recording(speech_web, speech_app) -> None:
    web, speech = speech_web
    app, _ = speech_app
    app.state.settings = app.state.settings.model_copy(update={"stt_max_bytes": 16})
    assert (await web.post("/api/auth/register", json=ANN)).status_code == 201

    response = await web.post(
        "/api/lesson/transcribe", json={"audio": BASE64_AUDIO, "format": "webm"}
    )

    assert response.status_code == 413
    assert "слишком длинная" in response.json()["detail"]
    assert speech.calls == []


async def test_provider_failure_reaches_the_student(speech_web, speech_app) -> None:
    web, speech = speech_web
    speech.error = STTError("Распознавание речи недоступно (HTTP 500): boom", status=500)
    assert (await web.post("/api/auth/register", json=ANN)).status_code == 201

    response = await web.post(
        "/api/lesson/transcribe", json={"audio": BASE64_AUDIO, "format": "webm"}
    )

    assert response.status_code == 502
    assert "недоступно" in response.json()["detail"]


async def test_transcriptions_per_student_are_limited(speech_web, speech_app) -> None:
    web, speech = speech_web
    app, _ = speech_app
    app.state.stt_limiter = app.state.stt_limiter.__class__(limit=2)
    assert (await web.post("/api/auth/register", json=ANN)).status_code == 201
    payload = {"audio": BASE64_AUDIO, "format": "webm"}

    assert (await web.post("/api/lesson/transcribe", json=payload)).status_code == 200
    assert (await web.post("/api/lesson/transcribe", json=payload)).status_code == 200
    blocked = await web.post("/api/lesson/transcribe", json=payload)

    assert blocked.status_code == 429
    assert len(speech.calls) == 2
