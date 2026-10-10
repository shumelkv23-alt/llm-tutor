"""Регрессии по ревью срезов 42–43 в браузере."""

import pytest
from playwright.async_api import expect

from helpers import enroll, finish_survey_via_api, sign_up, take_or_skip

pytestmark = pytest.mark.e2e


@pytest.fixture
async def enrolled(page, live_server):
    await sign_up(page, live_server)
    await enroll(page, live_server)
    return page


async def test_survey_back_failure_keeps_answers_in_sync(enrolled) -> None:
    page = enrolled
    await page.click("text=Поехали")
    for _ in range(2):
        await page.wait_for_selector("#survey[aria-busy=false] .survey-option")
        await page.locator(".survey-option").nth(1).click()
    await page.wait_for_selector("#survey[aria-busy=false] .survey-option")
    shown = await page.text_content("#survey-question")

    async def abort(route):
        await route.abort()

    await page.route("**/survey/next", abort)
    await page.click("text=‹ Назад")
    await page.wait_for_selector("#survey[aria-busy=false]")
    await page.unroute("**/survey/next", abort)
    assert await page.text_content("#survey-question") == shown

    requests = []
    page.on("request", lambda request: requests.append(request.post_data) if request.url.endswith("/survey/next") else None)
    await page.locator(".survey-option").nth(1).click()
    await page.wait_for_selector("#survey[aria-busy=false]")
    # Ответ ушёл третьим — к тому вопросу, что был на экране.
    assert requests and '"answers":[1,1,1]' in requests[-1].replace(" ", "")


async def _short_task(page):
    await page.click("#tab-btn-practice")
    for _ in range(15):
        if await page.locator("input.practice-input").count():
            return
        await take_or_skip(page)
    pytest.skip("не выпало задание с коротким ответом")


async def test_practice_draft_survives_chat_turn(enrolled) -> None:
    page = enrolled
    await finish_survey_via_api(page)
    await _short_task(page)
    await page.fill("input.practice-input", "мой недописанный ответ")

    await page.fill("#chat-input", "подскажи")
    await page.keyboard.press("Enter")
    await page.wait_for_selector("#lesson:not(.busy)")

    assert await page.input_value("input.practice-input") == "мой недописанный ответ"


async def test_choice_options_follow_arrow_keys(enrolled) -> None:
    page = enrolled
    await finish_survey_via_api(page)
    await page.click("#tab-btn-practice")
    for _ in range(15):
        if await page.locator(".practice-option").count():
            break
        await take_or_skip(page)
    options = page.locator(".practice-option")
    await options.first.focus()

    await page.keyboard.press("ArrowDown")

    await expect(options.nth(0)).to_have_attribute("aria-checked", "true")
    await page.keyboard.press("ArrowDown")
    await expect(options.nth(1)).to_have_attribute("aria-checked", "true")
    await expect(options.nth(1)).to_be_focused()
    assert await options.nth(0).get_attribute("tabindex") == "-1"


async def test_mobile_task_chip_shows_practice(enrolled) -> None:
    page = enrolled
    await finish_survey_via_api(page)
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.click("#view-chat")

    await page.click(".chip[data-action=task]")
    await page.wait_for_selector("#lesson:not(.busy)")

    assert await page.is_visible("#tab-practice")
    assert await page.get_attribute("#lesson", "data-view") == "lesson"


async def test_verify_button_keeps_focus(enrolled) -> None:
    page = enrolled
    await finish_survey_via_api(page)
    await page.focus("#verify-topic")

    await page.keyboard.press("Enter")
    await page.wait_for_selector("#lesson:not(.busy)")

    await expect(page.locator("#verify-topic")).to_be_focused()
