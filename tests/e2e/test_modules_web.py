"""Модули и урок частями в браузере (срез 52)."""

import pytest
from playwright.async_api import expect

from helpers import enroll, sign_up

pytestmark = pytest.mark.e2e


@pytest.fixture
async def enrolled(page, live_server):
    await sign_up(page, live_server)
    await enroll(page, live_server)
    return page


async def _survey(page) -> None:
    await page.click("text=Поехали")
    for _ in range(10):
        await page.wait_for_selector(".survey-option, .survey-summary")
        if await page.locator(".survey-summary").count():
            break
        await page.locator(".survey-option").nth(1).click()
        await page.wait_for_selector("#survey[aria-busy=false]")
    await page.click("text=Начать урок")
    await page.wait_for_selector("#pane-left:not(.surveying)")


async def test_survey_greets_with_the_module(enrolled) -> None:
    page = enrolled

    await expect(page.locator("#survey .eyebrow")).to_have_text("Модуль 1")
    await expect(page.locator("#survey h2")).to_contain_text("Pandas")


async def test_lesson_button_gives_the_question(enrolled) -> None:
    """После урока частями «Проверим» выдаёт вопрос во вкладку «Практика»."""
    page = enrolled
    await _survey(page)

    button = page.locator("#lesson-next")
    await expect(button).to_be_visible()
    await expect(button).to_contain_text("Проверим")
    async with page.expect_response("**/lesson/next"):
        await button.click()
    await page.wait_for_selector("#lesson:not(.busy)")

    await expect(page.locator(".task-prompt")).to_be_visible()
    await expect(button).to_be_hidden()  # пока висит вопрос, кнопки урока нет
    assert page.errors == []


async def test_route_groups_topics_by_module_and_jump_opens_module_survey(enrolled) -> None:
    page = enrolled
    await _survey(page)

    await page.click("#route-open")
    await page.wait_for_selector("#route-dialog[open]")
    headers = page.locator(".route-module")
    assert await headers.count() == 5
    await expect(headers.nth(1)).to_contain_text("Модуль 2")

    # Тема модуля 2 — сначала его анкета.
    target = page.locator('.route-topic[data-node="df_plot"]')
    async with page.expect_response("**/lesson/switch"):
        await target.click()
    await page.wait_for_selector("#pane-left.surveying")
    await expect(page.locator("#survey .eyebrow")).to_have_text("Модуль 2")

    # Случайный прыжок не запирает в анкете: можно вернуться (аудит среза 52, M2).
    leave = page.locator(".survey-leave")
    await expect(leave).to_have_text("‹ Вернуться к модулю 1")
    async with page.expect_response("**/lesson/switch"):
        await leave.click()
    await page.wait_for_selector("#pane-left:not(.surveying)")
    assert page.errors == []
