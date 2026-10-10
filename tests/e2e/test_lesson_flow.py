"""Анкета, практика и маршрут в браузере (Срез 43)."""

import pytest
from playwright.async_api import expect

from helpers import enroll, sign_up, take_or_skip

pytestmark = pytest.mark.e2e


@pytest.fixture
async def enrolled(page, live_server):
    await sign_up(page, live_server)
    await enroll(page, live_server)
    return page


async def _survey(page) -> None:
    await page.click("text=Поехали")
    # На каждый вопрос — второй вариант, пока не появится сводка.
    for _ in range(10):
        await page.wait_for_selector(".survey-option, .survey-summary")
        if await page.locator(".survey-summary").count():
            break
        await page.locator(".survey-option").nth(1).click()
        await page.wait_for_selector("#survey[aria-busy=false]")
    await page.click("text=Начать урок")
    await page.wait_for_selector("#pane-left:not(.surveying)")


async def test_survey_back_and_finish(enrolled) -> None:
    page = enrolled
    assert await page.is_disabled("#chat-input")

    await page.click("text=Поехали")
    await page.wait_for_selector(".survey-option")
    first = await page.text_content("#survey-question")
    await page.locator(".survey-option").nth(1).click()
    await page.wait_for_selector("text=‹ Назад")
    await page.click("text=‹ Назад")
    await expect(page.locator("#survey-question")).to_have_text(first)

    await page.reload()
    await page.wait_for_selector("#lesson[aria-busy=false]")
    await _survey(page)

    assert not await page.is_disabled("#chat-input")
    assert await page.locator(".msg.assistant").count() >= 2
    assert "Ближайшие шаги" in await page.locator(".msg.assistant").first.inner_text()
    assert page.errors == []


async def test_practice_choice_verdict(enrolled) -> None:
    page = enrolled
    await _survey(page)
    await page.click("#tab-btn-practice")

    for _ in range(12):
        if await page.locator(".practice-option").count():
            break
        await take_or_skip(page)
    assert await page.locator(".practice-option").count()

    send = page.locator(".practice-answer button[type=submit]")
    assert await send.is_disabled()
    await page.locator(".practice-option").first.click()
    assert await page.get_attribute(".practice-option >> nth=0", "aria-checked") == "true"
    await send.click()
    await page.wait_for_selector(".verdict")

    assert await page.locator(".verdict.ok, .verdict.bad").count() == 1
    assert page.errors == []


async def test_route_switches_topic(enrolled) -> None:
    page = enrolled
    await _survey(page)

    await page.click("#route-open")
    await page.wait_for_selector("#route-dialog[open]")
    rows = page.locator(".route-topic")
    assert await rows.count() == 21
    target = rows.nth(5)
    name = (await target.locator(".route-name").inner_text()).split(". ", 1)[1]
    await target.click()
    if await page.locator("#confirm-dialog[open]").count():
        await page.click("#confirm-submit")
    await expect(page.locator("#topic-title")).to_have_text(name)

    assert not await page.locator("#route-dialog[open]").count()
    assert "Перейти к теме" in await page.locator(".msg.user").last.inner_text()
