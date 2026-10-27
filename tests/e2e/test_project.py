"""Проект по курсу в браузере (срезы 97–101): выбор описания, идея, этапы, «Готов»."""

import pytest
from helpers import enroll, pass_course, sign_up
from playwright.async_api import expect

pytestmark = pytest.mark.e2e

CARD = {
    "problem": "Предсказать, уйдёт ли клиент в следующем месяце",
    "data": "Telco Customer Churn, около семи тысяч клиентов",
    "success": "ROC AUC выше, чем у логистической регрессии",
}
NOTES = {
    "data": {"done": "Загрузил данные и посмотрел типы", "findings": "Уходит около четверти клиентов"},
    "baseline": {"model": "Логистическая регрессия на всех признаках", "metric": "ROC AUC 0.84 на тесте"},
    "improve": {"tried": "Случайный лес с подбором глубины", "result": "Лес даёт ROC AUC 0.87"},
    "conclusions": {"answer": "Уход предсказуем по типу договора", "limits": "Данные одной компании"},
}


@pytest.fixture
async def course_passed(page, live_server, tmp_path):
    await sign_up(page, live_server)
    await enroll(page, live_server)
    pass_course(tmp_path / "users")
    return page


async def test_project_flow(course_passed, live_server) -> None:
    page = course_passed
    await page.goto(f"{live_server}/project")
    await page.wait_for_selector("#projects[aria-busy=false]")
    await expect(page.locator(".brief-card")).to_have_count(3)
    await expect(page.locator(".brief-card").first).to_contain_text("Прогноз оттока клиентов")

    await page.locator(".brief-card").first.get_by_role("button", name="Выбрать").click()
    await page.wait_for_url(f"{live_server}/project/mlcourse")
    await page.wait_for_selector("#project[aria-busy=false]")
    await expect(page.locator("#project-meta")).to_contain_text("Идея")
    await expect(page.locator("#chat-log")).to_contain_text("Прогноз оттока клиентов")

    await page.fill("#chat-input", "Хочу про отток клиентов")
    async with page.expect_response("**/project/chat"):
        await page.press("#chat-input", "Enter")
    await expect(page.locator("#chat-log")).to_contain_text("Какие данные у вас есть?")
    async with page.expect_response("**/project/take"):
        await page.get_by_role("button", name="Взять в карточку").click()
    await expect(page.locator("#f-card-title")).to_have_value("Отток клиентов")

    # Аудит среза 101: после «Взять в карточку» с полным предложением «Утвердить» сразу активна.
    approve = page.get_by_role("button", name="Утвердить идею")
    await expect(page.locator("#f-card-problem")).to_have_value(CARD["problem"])
    await expect(approve).to_be_enabled()
    # Поле, испорченное руками, гасит кнопку, исправленное — возвращает.
    await page.fill("#f-card-problem", "коротко")
    await expect(approve).to_be_disabled()
    await page.fill("#f-card-problem", CARD["problem"])
    await expect(approve).to_be_enabled()
    # «Сохранить карточку» перерисовывает форму, кнопка остаётся активной.
    async with page.expect_response("**/project/card"):
        await page.get_by_role("button", name="Сохранить карточку").click()
    await expect(approve).to_be_enabled()
    async with page.expect_response("**/project/approve"):
        await approve.click()
    await expect(page.locator("#project-meta")).to_contain_text("Реализация")
    await expect(page.locator(".project-stage[data-status=current]")).to_contain_text("Данные и анализ")

    for stage, fields in NOTES.items():
        complete = page.get_by_role("button", name="Этап выполнен")
        await expect(complete).to_be_disabled()
        for key, text in fields.items():
            await page.fill(f"#f-notes-{stage}-{key}", text)
        await expect(complete).to_be_enabled()
        async with page.expect_response("**/project/complete"):
            await complete.click()
        first_note = next(iter(fields.values()))
        await expect(page.locator(".project-stage[data-status=done]", has_text=first_note)).to_be_visible()

    await expect(page.locator("#project-meta")).to_contain_text("Готов")
    await expect(page.locator(".project-stage[data-status=done]")).to_have_count(4)
    await expect(page.locator(".project-card")).to_contain_text("Отток клиентов")
    assert page.errors == []

    await page.goto(f"{live_server}/project")
    await page.wait_for_selector("#projects[aria-busy=false]")
    await expect(page.locator(".project-course")).to_contain_text("Готов")


async def test_project_narrow_screen_switch(course_passed, live_server) -> None:
    page = course_passed
    await page.set_viewport_size({"width": 600, "height": 900})
    await page.goto(f"{live_server}/project/mlcourse")
    await page.wait_for_selector("#project[aria-busy=false]")
    await expect(page.locator("#pane-chat")).to_be_hidden()
    await page.click("#view-chat")
    await expect(page.locator("#pane-chat")).to_be_visible()
    await expect(page.locator("#pane-left")).to_be_hidden()
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
