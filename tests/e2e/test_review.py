"""Повторение по расписанию в браузере (срезы 79–83, фикстура занятий)."""

import pytest
from helpers import close_first_lesson, enroll, make_due, show_view, sign_up, survey_ui
from playwright.async_api import expect

pytestmark = pytest.mark.e2e


@pytest.fixture
async def student_page(page, lessons_server):
    await sign_up(page, lessons_server)
    await enroll(page, lessons_server)
    await survey_ui(page)
    return page


async def test_warmup_between_lessons(student_page, tmp_path) -> None:
    page = student_page
    make_due(tmp_path / "users", "l1_group")

    await close_first_lesson(page)

    # Вместо начала следующего занятия — разминка в «Практике».
    plate = page.locator(".review-plate")
    await expect(plate).to_contain_text("Повторение · занятие «Группировка» · 1 из 1")
    await expect(plate.locator("button")).to_have_text("Пропустить разминку")
    await expect(page.locator("#topic-title")).to_have_text("Выборка данных")
    await expect(page.locator(".task-streak")).to_have_count(0)

    await page.locator(".practice-option").first.click()
    async with page.expect_response("**/lesson/answer"):
        await page.click(".practice-answer button[type=submit]")
    await page.wait_for_selector("#lesson:not(.busy)")

    # Разминка кончилась: плашки нет, началось следующее занятие.
    await expect(page.locator(".review-plate")).to_have_count(0)
    await show_view(page, "chat")
    await expect(page.locator(".chat-log")).to_contain_text("Повторение закончено: верно 1 из 1")
    await expect(page.locator("#lesson-next")).to_be_visible()
    assert page.errors == []


async def test_skip_warmup_starts_lesson(student_page, tmp_path) -> None:
    page = student_page
    make_due(tmp_path / "users", "l1_group")
    await close_first_lesson(page)
    await expect(page.locator(".review-plate")).to_be_visible()

    async with page.expect_response("**/lesson/review/stop"):
        await page.click(".review-plate button")
    await page.wait_for_selector("#lesson:not(.busy)")

    await expect(page.locator(".review-plate")).to_have_count(0)
    await show_view(page, "chat")
    await expect(page.locator("#lesson-next")).to_be_visible()
    assert page.errors == []


async def test_review_tab_runs_a_series(student_page, tmp_path) -> None:
    page = student_page
    make_due(tmp_path / "users", "l1_group")
    await page.reload()
    await page.wait_for_selector("#lesson[aria-busy=false]")

    await page.click("#tab-btn-review")
    panel = page.locator("#tab-review")
    await expect(panel).to_contain_text("Срочное: 1")
    await expect(panel.locator(".review-lesson")).to_have_count(1)
    await expect(panel.locator(".review-lesson")).to_contain_text("Группировка")
    await expect(panel.locator(".review-lesson")).to_contain_text("пора повторить")

    async with page.expect_response("**/lesson/review/start"):
        await panel.locator("button:has-text('Начать')").click()
    await page.wait_for_selector("#lesson:not(.busy)")

    # Задание серии — в «Практике», у плашки «Закончить».
    await expect(page.locator("#tab-btn-practice")).to_have_attribute("aria-selected", "true")
    plate = page.locator(".review-plate")
    await expect(plate).to_contain_text("Повторение · занятие «Группировка» · 1 из 1")
    await expect(plate.locator("button")).to_have_text("Закончить")

    await page.locator(".practice-option").first.click()
    async with page.expect_response("**/lesson/answer"):
        await page.click(".practice-answer button[type=submit]")
    await page.wait_for_selector("#lesson:not(.busy)")

    await show_view(page, "chat")
    await expect(page.locator(".chat-log")).to_contain_text("Повторение закончено: верно 1 из 1")
    await show_view(page, "lesson")
    await page.click("#tab-btn-review")
    await expect(panel).to_contain_text("Срочное: 0")
    assert page.errors == []


async def test_hash_opens_review_tab_and_switch_persists(student_page, tmp_path, lessons_server) -> None:
    page = student_page
    make_due(tmp_path / "users", "l1_group")

    await page.goto(f"{lessons_server}/lesson/mlcourse#review")
    await page.wait_for_selector("#lesson[aria-busy=false]")
    await expect(page.locator("#tab-btn-review")).to_have_attribute("aria-selected", "true")

    switch = page.locator(".review-switch input")
    await expect(switch).to_be_checked()
    async with page.expect_response("**/lesson/review/settings"):
        await switch.uncheck()
    await page.wait_for_selector("#lesson:not(.busy)")
    await page.reload()
    await page.wait_for_selector("#lesson[aria-busy=false]")
    await page.click("#tab-btn-review")
    await expect(page.locator(".review-switch input")).not_to_be_checked()

    await page.goto(f"{lessons_server}/")
    await expect(page.locator("body")).not_to_contain_text("Повторить сегодня")
    assert page.errors == []


async def test_home_block_leads_to_review_tab(student_page, tmp_path, lessons_server) -> None:
    page = student_page
    make_due(tmp_path / "users", "l1_group")

    await page.goto(f"{lessons_server}/")
    await expect(page.locator(".review-today")).to_contain_text("Повторить сегодня")
    await expect(page.locator(".review-today")).to_contain_text("1 занятие")
    await expect(page.locator(".course-review").first).to_contain_text("к повторению: 1")
    await page.click(".review-today a")
    await page.wait_for_selector("#lesson[aria-busy=false]")
    await expect(page.locator("#tab-btn-review")).to_have_attribute("aria-selected", "true")
    assert page.errors == []


async def test_review_fits_320px(browser, lessons_server, tmp_path) -> None:
    context = await browser.new_context(viewport={"width": 320, "height": 700})
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await sign_up(page, lessons_server)
    await enroll(page, lessons_server)
    await survey_ui(page)
    make_due(tmp_path / "users", "l1_group")
    await page.reload()
    await page.wait_for_selector("#lesson[aria-busy=false]")

    await page.click("#tab-btn-review")
    await expect(page.locator("#tab-review")).to_contain_text("Срочное: 1")
    assert await page.evaluate("document.documentElement.scrollWidth") <= 320

    async with page.expect_response("**/lesson/review/start"):
        await page.locator("#tab-review button:has-text('Начать')").click()
    await page.wait_for_selector("#lesson:not(.busy)")
    await expect(page.locator(".review-plate")).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth") <= 320

    await page.goto(f"{lessons_server}/")
    assert await page.evaluate("document.documentElement.scrollWidth") <= 320
    assert errors == []
    await context.close()
