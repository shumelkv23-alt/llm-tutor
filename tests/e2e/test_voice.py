"""Голосовой ввод в чате: микрофон заполняет поле, но не отправляет реплику.

Настоящий Web Speech API в тесте не нужен: подставляем SpeechRecognition,
который сразу отдаёт финальный результат. Проверяем именно нашу обвязку —
кнопку, вставку текста и запрет автоотправки.
"""

import pytest
from playwright.async_api import expect

from helpers import enroll, finish_survey_via_api, sign_up

pytestmark = pytest.mark.e2e

FAKE = """
window.__voiceText = "Что такое DataFrame?";
window.SpeechRecognition = class {
  constructor() {
    this.lang = "";
    this.continuous = false;
    this.interimResults = false;
    this.maxAlternatives = 1;
  }
  start() {
    setTimeout(() => {
      this.onstart && this.onstart();
      const result = [{ transcript: window.__voiceText }];
      result.isFinal = true;
      this.onresult && this.onresult({ resultIndex: 0, results: [result] });
    }, 20);
  }
  stop() { setTimeout(() => this.onend && this.onend(), 10); }
  abort() { setTimeout(() => this.onend && this.onend(), 10); }
};
window.webkitSpeechRecognition = window.SpeechRecognition;
window.__spoken = [];
let __ttsCurrent = null;
const FakeUtterance = function (text) {
  this.text = text;
  this.lang = "";
  this.voice = null;
  this.onend = null;
  this.onerror = null;
};
const fakeSynth = {
  getVoices: () => [{ lang: "ru-RU", name: "Russian" }],
  cancel: () => {
    const old = __ttsCurrent;
    __ttsCurrent = null;
    if (old && old.onerror) old.onerror({ error: "interrupted" });
  },
  speak: (utterance) => {
    __ttsCurrent = utterance;
    window.__spoken.push(utterance.text);
    setTimeout(() => {
      if (__ttsCurrent === utterance) {
        __ttsCurrent = null;
        if (utterance.onend) utterance.onend();
      }
    }, 40);
  },
};
Object.defineProperty(window, "SpeechSynthesisUtterance", { value: FakeUtterance, configurable: true });
Object.defineProperty(window, "speechSynthesis", { value: fakeSynth, configurable: true });
"""


async def test_chat_mic_fills_input_without_sending(page, live_server) -> None:
    await page.add_init_script(FAKE)
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)

    await page.wait_for_selector("#chat-mic:not([hidden])")
    await page.click("#chat-mic")

    await expect(page.locator("#chat-input")).to_have_value("Что такое DataFrame?")
    # Реплика не ушла в журнал: отправку делает ученик, а не распознаватель.
    assert await page.locator("#chat-log .msg.user", has_text="DataFrame").count() == 0

    # «Отправить» во время записи останавливает её, но не отправляет
    # недослушанный текст — guard в chat.js.
    await page.click('#chat-form button[type="submit"]')
    await page.wait_for_timeout(100)
    assert await page.input_value("#chat-input") == "Что такое DataFrame?"
    assert await page.locator("#chat-log .msg.user", has_text="DataFrame").count() == 0
    assert page.errors == []


async def test_tts_reads_reply_and_theory(page, live_server) -> None:
    """Озвучивание ответа и конспекта: кнопки есть, текст уходит в синтез."""
    await page.add_init_script(FAKE)
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)

    await page.click("#chat-log .msg.assistant .speech-btn")
    await page.wait_for_function("() => window.__spoken.length >= 1")
    assert await page.evaluate("() => window.__spoken[0]")

    await page.click("#tab-btn-theory")
    await page.wait_for_selector(".theory-tools .speech-btn")
    await page.click(".theory-tools .speech-btn")
    await page.wait_for_function("() => window.__spoken.length >= 2")
    assert await page.evaluate("() => window.__spoken[1]")
    assert page.errors == []
