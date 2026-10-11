"""Занятие из разделов в браузере на узком экране (срез 55, фикстура занятий)."""

import pytest
from playwright.async_api import expect

from helpers import enroll, sign_up

pytestmark = pytest.mark.e2e


@pytest.fixture
async def narrow(browser, lessons_server):
    context = await browser.new_context(viewport={"width": 320, "height": 700})
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    await sign_up(page, lessons_server)
    await enroll(page, lessons_server)
    yield page
    await context.close()


async def _survey(page) -> None:
    await page.click("text=Поехали")
    for _ in range(10):
        await page.wait_for_selector(".survey-option, .survey-summary")
        if await page.locator(".survey-summary").count():
            break
        # «Впервые вижу»/«С нуля»: занятия не помечаются знакомыми.
        await page.locator(".survey-option").first.click()
        await page.wait_for_selector("#survey[aria-busy=false]")
    await page.click("text=Начать урок")
    await page.wait_for_selector("#pane-left:not(.surveying)")


async def _check(page) -> None:
    """Кнопка урока в чате → вопрос во вкладке «Практика»."""
    await page.click("#view-chat")
    button = page.locator("#lesson-next")
    await expect(button).to_contain_text("Проверим")
    async with page.expect_response("**/lesson/next"):
        await button.click()
    await page.wait_for_selector("#lesson:not(.busy)")
    await page.click("#view-lesson")
    await page.click("#tab-btn-practice")


async def _answer_first_option(page) -> None:
    await page.locator(".practice-option").first.click()
    async with page.expect_response("**/lesson/answer"):
        await page.click(".practice-answer button[type=submit]")
    await page.wait_for_selector("#lesson:not(.busy)")


async def test_lesson_of_two_sections_and_final_at_320px(narrow) -> None:
    page = narrow
    await _survey(page)

    meta = page.locator("#topic-meta")
    await expect(meta).to_contain_text("Модуль 1 · Занятие 1 из 3 · Раздел 1 из 2: Series")
    strip = page.locator("#section-strip .section-step")
    await expect(strip).to_have_count(3)
    await expect(strip.nth(0)).to_have_class("section-step current")
    await expect(page.locator(".theory-toc .toc-item")).to_have_count(2)

    await _check(page)
    await expect(page.locator(".task-part")).to_have_text("Раздел 1: Series")
    await _answer_first_option(page)  # верный вариант в фикстуре — первый
    await expect(meta).to_contain_text("Раздел 2 из 2: DataFrame")
    await expect(strip.nth(0)).to_have_class("section-step passed")

    await _check(page)
    await expect(page.locator(".task-part")).to_have_text("Раздел 2: DataFrame")
    await _answer_first_option(page)
    # Итоговое — сразу, без урока: короткий ответ.
    await expect(page.locator(".task-part")).to_have_text("Итоговое задание")
    await expect(meta).to_contain_text("Итоговое задание")
    await page.fill("input.practice-input", "final_l1_frames")
    async with page.expect_response("**/lesson/answer"):
        await page.click(".practice-answer button[type=submit]")
    await page.wait_for_selector("#lesson:not(.busy)")

    await expect(meta).to_contain_text("Занятие 2 из 3 · Раздел 1 из 2: loc и iloc")
    assert await page.evaluate("document.documentElement.scrollWidth") <= 320
    assert page.errors == []


async def test_route_collapses_modules_and_marks_progress(narrow) -> None:
    page = narrow
    await _survey(page)

    await page.click("#route-open")
    await page.wait_for_selector("#route-dialog[open]")
    toggles = page.locator(".route-module-toggle")
    await expect(toggles).to_have_count(2)
    await expect(toggles.nth(0)).to_have_attribute("aria-expanded", "true")
    await expect(toggles.nth(1)).to_have_attribute("aria-expanded", "false")
    await expect(page.locator(".route-topic")).to_have_count(3)
    await toggles.nth(1).click()
    await expect(page.locator(".route-topic")).to_have_count(6)
    await expect(page.locator("#route-summary")).to_have_text("Модуль 1: закрыто 0 из 3")
    assert await page.evaluate("document.documentElement.scrollWidth") <= 320
    assert page.errors == []
