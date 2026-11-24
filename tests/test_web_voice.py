"""Голосовой ввод: подключение в шаблонах и контракт клиентского модуля.

Поведение Web Speech API в браузере проверяется e2e; здесь — то, что можно
проверить без браузера: страницы подключают модуль, кнопки скрыты до проверки
поддержки, а сам модуль не вставляет HTML и не делает сетевых запросов.
"""

from llm_tutor.web.app import STATIC_DIR, TEMPLATES_DIR


def _template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _voice_js() -> str:
    return (STATIC_DIR / "js" / "voice.js").read_text(encoding="utf-8")


def test_lesson_wires_voice_input() -> None:
    html = _template("lesson.html")

    assert "static_url('js/voice.js')" in html
    assert 'id="chat-mic"' in html
    assert "icon('mic')" in html
    # Кнопка появляется, только когда Voice.attach подтвердил поддержку.
    assert 'aria-pressed="false"' in html and "hidden" in html


def test_admin_wires_voice_input() -> None:
    for name in ("admin_courses.html", "admin_course.html"):
        html = _template(name)
        assert "static_url('js/voice.js')" in html, name

    courses = _template("admin_courses.html")
    assert 'data-voice="description"' in courses

    course = _template("admin_course.html")
    assert 'data-voice="description"' in course
    for element_id in (
        "voice-brief",
        "voice-brief-start",
        "voice-brief-text",
        "voice-brief-save",
        "voice-brief-clear",
    ):
        assert f'id="{element_id}"' in course, element_id


def test_admin_voice_buttons_carry_recording_style() -> None:
    """У админских кнопок тот же класс, что рисует красную запись, — иначе
    визуально ничего не меняется, хотя aria-pressed и live-регион есть."""
    for name in ("admin_courses.html", "admin_course.html"):
        html = _template(name)
        assert 'class="btn voice-btn" type="button" data-voice="description"' in html, name

    course = _template("admin_course.html")
    assert 'class="btn voice-btn" type="button" id="voice-brief-start"' in course


def test_tts_is_wired_into_chat_and_theory() -> None:
    """Озвучивание — браузерный speechSynthesis, без сервера и ключей."""
    js = _voice_js()
    assert "speechSynthesis" in js and "SpeechSynthesisUtterance" in js
    assert "ttsSupported" in js and "function speak(" in js
    for name in ("chat.js", "theory.js"):
        source = (STATIC_DIR / "js" / name).read_text(encoding="utf-8")
        assert "Voice.speak" in source or "Voice?.speak" in source, name
    # Иконка должна быть в спрайте: общий тест это тоже поймает.
    assert "speaker" in (STATIC_DIR / "icons.svg").read_text(encoding="utf-8")


def test_voice_module_contract() -> None:
    js = _voice_js()

    # Оба имени: Chrome/Edge исторически отдают webkit-вариант.
    assert "SpeechRecognition" in js and "webkitSpeechRecognition" in js
    assert '"ru-RU"' in js
    # События, по которым чат не отправляет недослушанный текст.
    assert "voice:start" in js and "voice:stop" in js
    assert "aria-pressed" in js
    # Ошибки распознавания объясняются ученику, а не молчат.
    assert "not-allowed" in js and "no-speech" in js
    # В DOM модуль пишет только текст: HTML приходит с сервера.
    assert "innerHTML" not in js
    assert "eval(" not in js


async def test_microphone_is_allowed_by_permissions_policy(web) -> None:
    """Голосовому вводу нужен микрофон, и только своему origin."""
    response = await web.get("/")

    assert response.headers["permissions-policy"] == "microphone=(self)"


def test_voice_buttons_start_hidden() -> None:
    """В браузере без распознавания не должно остаться нерабочих кнопок."""
    lesson = _template("lesson.html")
    chat_mic = lesson.split('id="chat-mic"', 1)[1].split(">", 1)[0]
    assert "hidden" in chat_mic

    for name in ("admin_courses.html", "admin_course.html"):
        html = _template(name)
        button = html.split('data-voice="description"', 1)[1].split(">", 1)[0]
        assert "hidden" in button, name

    brief = _template("admin_course.html").split('id="voice-brief"', 1)[1].split(">", 1)[0]
    assert "hidden" in brief
