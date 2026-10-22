"""Плашка фрагмента в чате: прикрепить, отправить, перезагрузить (срез 77)."""

import json

import pytest
from playwright.async_api import expect

from helpers import enroll, finish_survey_via_api, sign_up

pytestmark = pytest.mark.e2e


@pytest.fixture
async def lesson(page, live_server):
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    return page


async def _attach(page, source: str, text: str) -> None:
    await page.evaluate("([source, text]) => Lesson.chat.attach({source, text})", [source, text])


async def _send(page, question: str) -> dict:
    await page.fill("#chat-input", question)
    async with page.expect_request("**/lesson/chat") as request:
        await page.press("#chat-input", "Enter")
    return (await request.value).post_data_json


async def _idle(page) -> None:
    await page.wait_for_function("() => !Lesson.busy")


async def test_attached_quote_goes_with_question(lesson) -> None:
    page = lesson
    await _attach(page, "theory", "groupby делит строки")

    quote = page.locator("#chat-quote")
    await expect(quote).to_be_visible()
    await expect(quote).to_contain_text("Из теории")
    await expect(quote).to_contain_text("groupby делит строки")
    await expect(page.locator("#chat-input")).to_be_focused()
    await expect(page.locator("#chat-quote-status")).to_have_text("Фрагмент прикреплён: из теории")

    body = await _send(page, "почему так?")

    assert body == {"text": "почему так?", "quote": {"source": "theory", "text": "groupby делит строки"}}
    await expect(quote).to_be_hidden()
    await _idle(page)
    figure = page.locator("#chat-log .msg.user .msg-quote").last
    await expect(figure.locator("figcaption")).to_have_text("Из теории")
    await expect(figure).to_contain_text("groupby делит строки")

    await page.reload()
    await page.wait_for_selector("#lesson[aria-busy=false]")
    figure = page.locator("#chat-log .msg.user .msg-quote").last
    await expect(figure.locator("figcaption")).to_have_text("Из теории")
    await expect(page.locator("#chat-log .msg.user").last).to_contain_text("почему так?")
    assert page.errors == []


async def test_cross_removes_quote(lesson) -> None:
    page = lesson
    await _attach(page, "code", "df.head()")

    await page.click("#chat-quote-remove")

    await expect(page.locator("#chat-quote")).to_be_hidden()
    await expect(page.locator("#chat-input")).to_be_focused()
    body = await _send(page, "что дальше?")
    assert body == {"text": "что дальше?"}


async def test_chips_do_not_take_quote(lesson) -> None:
    page = lesson
    await _attach(page, "task", "найдите среднее")

    async with page.expect_request("**/lesson/chat") as request:
        await page.click("#chat-chips .chip[data-say]")

    assert "quote" not in (await request.value).post_data_json
    await expect(page.locator("#chat-quote")).to_be_visible()


async def test_new_attach_replaces_old(lesson) -> None:
    page = lesson
    await _attach(page, "theory", "первый")
    await _attach(page, "code", "второй")

    await expect(page.locator("#chat-quote")).to_contain_text("Из моего кода")
    await expect(page.locator("#chat-quote")).not_to_contain_text("первый")
    body = await _send(page, "а это?")
    assert body["quote"] == {"source": "code", "text": "второй"}


async def test_rejected_turn_restores_quote(lesson) -> None:
    page = lesson
    await page.route(
        "**/lesson/chat",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"detail": {"message": "нет"}}),
        ),
    )
    await _attach(page, "theory", "groupby делит строки")

    await _send(page, "почему так?")
    await _idle(page)

    await expect(page.locator("#chat-quote")).to_be_visible()
    await expect(page.locator("#chat-quote")).to_contain_text("groupby делит строки")
    await expect(page.locator("#chat-input")).to_have_value("почему так?")
    assert await page.locator("#chat-log .msg.user.pending").count() == 0


async def test_quote_markup_is_text(lesson) -> None:
    page = lesson
    payload = "<img src=x onerror=window.__x=1>"
    await _attach(page, "code", payload)
    await expect(page.locator("#chat-quote")).to_contain_text(payload)

    await _send(page, "что это?")
    await _idle(page)

    await expect(page.locator("#chat-log .msg.user .msg-quote").last).to_contain_text(payload)
    assert await page.evaluate("() => window.__x") is None
    assert await page.locator("#chat-log img").count() == 0
    assert page.errors == []


# --- аудит среза 77 ---


async def test_repeat_question_after_rejection_restores_quote(lesson) -> None:
    """Тот же вопрос, что прежний, не считается доставленным, если ход не принят."""
    page = lesson
    await _attach(page, "theory", "первый фрагмент")
    await _send(page, "почему?")
    await _idle(page)
    await page.route(
        "**/lesson/chat",
        lambda route: route.fulfill(
            status=503, content_type="application/json", body=json.dumps({"detail": {"message": "нет"}})
        ),
    )
    await _attach(page, "code", "второй фрагмент")

    await _send(page, "почему")
    await _idle(page)

    await expect(page.locator("#chat-input")).to_have_value("почему")
    await expect(page.locator("#chat-quote")).to_contain_text("второй фрагмент")


async def test_delivered_turn_is_not_restored(lesson) -> None:
    """Ход дошёл, а ответ оборвался: текст и плашка не возвращаются (не будет дубля)."""
    page = lesson

    async def deliver_then_drop(route):
        await route.fetch()
        await route.abort()

    await page.route("**/lesson/chat", deliver_then_drop)
    await _attach(page, "theory", "groupby")

    await _send(page, 'что значит "axis"?')
    await _idle(page)

    await expect(page.locator("#chat-input")).to_have_value("")
    await expect(page.locator("#chat-quote")).to_be_hidden()


async def test_chip_keeps_draft_and_quote(lesson) -> None:
    page = lesson
    await _attach(page, "theory", "groupby")
    await page.fill("#chat-input", "мой недописанный вопрос")

    async with page.expect_request("**/lesson/chat"):
        await page.click("#chat-chips .chip[data-say]")
    await _idle(page)

    await expect(page.locator("#chat-input")).to_have_value("мой недописанный вопрос")
    await expect(page.locator("#chat-quote")).to_be_visible()


async def test_reattach_is_announced_again(lesson) -> None:
    page = lesson
    await page.evaluate(
        """() => {
            window.__said = [];
            new MutationObserver(() => window.__said.push(
                document.getElementById('chat-quote-status').textContent
            )).observe(document.getElementById('chat-quote-status'),
                       {childList: true, characterData: true, subtree: true});
        }"""
    )
    await _attach(page, "theory", "первый")
    await page.wait_for_timeout(300)
    await _attach(page, "theory", "второй")
    await page.wait_for_timeout(300)

    # Диктор повторяет объявление, только если текст области менялся: между
    # двумя одинаковыми объявлениями она должна очиститься.
    said = await page.evaluate("() => window.__said")
    announced = "Фрагмент прикреплён: из теории"
    first = said.index(announced)
    assert "" in said[first + 1 :]
    assert said[-1] == announced


async def test_attach_does_nothing_before_survey(page, live_server) -> None:
    await sign_up(page, live_server)
    await enroll(page, live_server)

    await _attach(page, "theory", "groupby")

    await expect(page.locator("#chat-quote")).to_be_hidden()
