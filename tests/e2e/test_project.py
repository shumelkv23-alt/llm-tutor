"""Проект по курсу в браузере (срезы 97–101): выбор описания, идея, этапы, «Готов»."""

import pytest
from helpers import enroll, pass_course, sign_up
from playwright.async_api import expect

pytestmark = pytest.mark.e2e

@pytest.fixture
async def course_passed(page, live_server, tmp_path):
    await sign_up(page, live_server)
    await enroll(page, live_server)
    pass_course(tmp_path / "users")
    return page


async def _open_project(page, live_server) -> None:
    await page.goto(f"{live_server}/project")
    await page.wait_for_selector("#projects[aria-busy=false]")
    mlcourse = page.locator("[aria-labelledby=project-course-mlcourse]")
    await mlcourse.locator(".brief-card").first.get_by_role("button", name="Выбрать").click()
    await page.wait_for_url(f"{live_server}/project/mlcourse")
    await page.wait_for_selector("#project[aria-busy=false]")


async def _say(page, text: str) -> None:
    await page.fill("#chat-input", text)
    async with page.expect_response("**/project/chat"):
        await page.press("#chat-input", "Enter")


async def _to_build(page, live_server) -> None:
    """Выбор описания, предложение карточки, «Взять в карточку», «Утвердить идею»."""
    await _open_project(page, live_server)
    await expect(page.locator("#project-meta")).to_contain_text("Идея")
    await _say(page, "Хочу про отток клиентов")
    await expect(page.locator("#chat-log")).to_contain_text("Какие данные у вас есть?")
    async with page.expect_response("**/project/take"):
        await page.locator("[data-action=take]").click()
    await expect(page.locator(".project-card")).to_contain_text("Отток клиентов")
    async with page.expect_response("**/project/approve"):
        await page.locator("[data-action=approve]").click()
    await expect(page.locator("#project-meta")).to_contain_text("Реализация")
    await expect(page.locator("#project-steps [data-status=current]")).to_contain_text("Данные и анализ")


async def test_project_flow(course_passed, live_server) -> None:
    page = course_passed
    await page.goto(f"{live_server}/project")
    await page.wait_for_selector("#projects[aria-busy=false]")
    mlcourse = page.locator("[aria-labelledby=project-course-mlcourse]")
    await expect(mlcourse.locator(".brief-card")).to_have_count(3)
    await expect(mlcourse.locator(".brief-card").first).to_contain_text("Прогноз оттока клиентов")

    await _to_build(page, live_server)
    # Идея утверждена: старое предложение карточки в чате без кнопок.
    await expect(page.locator("[data-action=take]")).to_have_count(0)
    await expect(page.locator("#quick-reply")).to_have_text("Подвести итог этапа")

    talk = {
        "Данные и анализ": "Загрузил данные и посмотрел типы, уходит около четверти клиентов",
        "Базовая модель": "Логистическая регрессия на всех признаках, ROC AUC 0.84 на тесте",
        "Улучшения": "Случайный лес с подбором глубины, ROC AUC 0.87",
        "Выводы": "Уход предсказуем по типу договора, данные одной компании",
    }
    titles = list(talk)
    for index, (title, text) in enumerate(talk.items()):
        await expect(page.locator("#project-steps [data-status=current]")).to_contain_text(title)
        await _say(page, text)
        async with page.expect_response("**/project/accept"):
            await page.locator("[data-action=accept]").last.click()
        if index + 1 < len(titles):
            await expect(page.locator("#project-steps [data-status=current]")).to_contain_text(titles[index + 1])
            await expect(page.locator(".stage-done", has_text=title)).to_be_visible()

    await expect(page.locator("#project-meta")).to_contain_text("Готов")
    await expect(page.locator("#project-steps [data-status=done]")).to_have_count(5)
    await expect(page.locator(".stage-done")).to_have_count(4)
    await expect(page.locator("#quick-reply")).to_be_hidden()
    assert page.errors == []

    await page.goto(f"{live_server}/project")
    await page.wait_for_selector("#projects[aria-busy=false]")
    await expect(page.locator("[aria-labelledby=project-course-mlcourse]")).to_contain_text("Готов")


async def test_accept_missing_opens_dialog(course_passed, live_server) -> None:
    """Предложение без обязательного поля: 422 открывает «Поправить» с подсветкой, итог дописывается."""
    page = course_passed
    await _to_build(page, live_server)
    await _say(page, "Загрузил данные [один пункт]")
    async with page.expect_response("**/project/accept"):
        await page.locator("[data-action=accept]").last.click()

    dialog = page.locator("#stage-dialog")
    await expect(dialog).to_be_visible()
    await expect(dialog.locator("[data-key=done] textarea")).to_have_value("Загрузил данные и посмотрел типы")
    await expect(dialog.locator("[data-key=findings] textarea")).to_have_attribute("aria-invalid", "true")
    await dialog.locator("[data-key=findings] textarea").fill("Уходит около четверти клиентов")
    async with page.expect_response("**/project/complete"):
        await dialog.get_by_role("button", name="Сохранить и дальше").click()
    await expect(dialog).to_be_hidden()
    await expect(page.locator("#project-steps [data-status=current]")).to_contain_text("Базовая модель")
    assert [e for e in page.errors if "422" not in e] == []


async def test_manual_stage_without_model(course_passed, live_server) -> None:
    """Модель недоступна — этап закрывается через «Записать итог самому»."""
    page = course_passed
    await _to_build(page, live_server)
    await page.fill("#chat-input", "[сбой]")
    async with page.expect_response("**/project/chat"):
        await page.press("#chat-input", "Enter")
    await expect(page.locator("#chat-log .msg.failed")).to_have_count(1)

    await page.locator("[data-action=own-summary]").click()
    dialog = page.locator("#stage-dialog")
    await dialog.locator("[data-key=done] textarea").fill("Загрузил данные и посмотрел типы")
    await dialog.locator("[data-key=findings] textarea").fill("Классы несбалансированы")
    async with page.expect_response("**/project/complete"):
        await dialog.get_by_role("button", name="Сохранить и дальше").click()
    await expect(page.locator("#project-steps [data-status=current]")).to_contain_text("Базовая модель")
    assert [e for e in page.errors if "502" not in e] == []


async def test_project_narrow_screen_switch(course_passed, live_server) -> None:
    page = course_passed
    await _open_project(page, live_server)
    await page.set_viewport_size({"width": 600, "height": 900})
    await expect(page.locator("#pane-chat")).to_be_visible()
    await expect(page.locator("#pane-summary")).to_be_hidden()
    await page.click("#view-project")
    await expect(page.locator("#pane-summary")).to_be_visible()
    await expect(page.locator("#pane-chat")).to_be_hidden()
    assert page.errors == []


async def test_admin_nav_labels_fit_a_phone(page, live_server, tmp_path) -> None:
    """Шесть пунктов меню у админа на 320 px: страница не шире экрана, подписи не обрезаны."""
    from test_admin import _admin_page

    await page.set_viewport_size({"width": 320, "height": 700})
    student, _ = await _admin_page(page, live_server, tmp_path)

    assert await page.locator(".main-nav a").count() == 6
    assert await page.evaluate("() => document.documentElement.scrollWidth") <= 320
    clipped = await page.evaluate(
        "() => [...document.querySelectorAll('.main-nav a')]"
        ".filter((a) => a.scrollWidth > a.clientWidth).map((a) => a.textContent.trim())"
    )
    assert clipped == []
    student.close()
