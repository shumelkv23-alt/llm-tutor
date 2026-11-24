"""Голосовой ввод в браузере: подставное распознавание (Web Speech API)."""

import pytest

from helpers import enroll, finish_survey_via_api, sign_up

pytestmark = pytest.mark.e2e

# Настоящего микрофона в CI нет: подменяем SpeechRecognition до загрузки
# страницы. Тест проверяет нашу обвязку (кнопка, вставка, input-событие), а не
# службу распознавания браузера.
FAKE_RECOGNITION = """
class FakeSpeechRecognition {
  constructor() { window.__lastRecognition = this; }
  start() { this.started = true; }
  stop() { this.started = false; this.onend?.(); }
  abort() { this.started = false; }
}
window.SpeechRecognition = FakeSpeechRecognition;
window.webkitSpeechRecognition = FakeSpeechRecognition;
"""


@pytest.fixture
async def lesson(page, live_server):
    await page.add_init_script(FAKE_RECOGNITION)
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    await page.wait_for_selector("#lesson[aria-busy=false]")
    return page


async def test_mic_appears_and_dictates_into_chat(lesson) -> None:
    mic = lesson.locator("#chat-form .voice-btn")
    assert await mic.count() == 1
    assert await mic.is_visible()

    await mic.click()
    await lesson.evaluate(
        """() => {
          const result = [{ transcript: "Что такое groupby" }];
          result.isFinal = true;
          window.__lastRecognition.onresult({ resultIndex: 0, results: [result] });
        }"""
    )

    assert await lesson.input_value("#chat-input") == "Что такое groupby"
    # Кнопка в состоянии «слушаю», повторный клик завершает запись.
    assert await mic.get_attribute("aria-pressed") == "true"
    await mic.click()
    assert await mic.get_attribute("aria-pressed") == "false"
    assert lesson.errors == []
