"""Карта знаний в профиле в браузере (Срез 75)."""

import pytest
from helpers import enroll, finish_survey_via_api, sign_up
from playwright.async_api import expect

pytestmark = pytest.mark.e2e


async def _open_profile(page, live_server) -> None:
    await page.goto(f"{live_server}/profile")
    await expect(page.locator("#knowledge")).to_have_attribute("data-ready", "true")


async def test_graph_draws_and_switches_lesson(page, live_server) -> None:
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    await _open_profile(page, live_server)

    box = await page.locator("#kg-canvas").bounding_box()
    assert box["width"] > 200 and box["height"] > 200
    # На холсте что-то нарисовано: не все пиксели цвета фона.
    painted = await page.evaluate(
        """() => {
            const c = document.querySelector('#kg-canvas');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            const bg = [d[0], d[1], d[2]];
            let other = 0;
            for (let i = 0; i < d.length; i += 16) if (d[i] !== bg[0] || d[i + 1] !== bg[1] || d[i + 2] !== bg[2]) other++;
            return other;
        }"""
    )
    assert painted > 200

    await page.locator(".kg-list summary").click()
    target = page.locator("#kg-list button[data-node]").nth(5)
    name = await target.text_content()
    await target.click()
    card = page.locator("#kg-card")
    await expect(card).to_be_visible()
    await expect(card.locator("h3")).to_have_text(name)
    await expect(target).to_have_attribute("aria-pressed", "true")

    go = card.get_by_role("button", name="Перейти к занятию")
    async with page.expect_response("**/lesson/switch"):
        await go.click()
    await page.wait_for_url(f"{live_server}/lesson/mlcourse")
    await expect(page.locator("#topic-title")).to_contain_text(name)
    assert page.errors == []


async def test_graph_before_survey_has_no_switch(page, live_server) -> None:
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await _open_profile(page, live_server)

    await page.locator(".kg-list summary").click()
    await page.locator("#kg-list button[data-node]").first.click()
    card = page.locator("#kg-card")
    await expect(card).to_contain_text("после анкеты")
    await expect(card.get_by_role("button", name="Перейти к занятию")).to_have_count(0)


async def test_graph_fits_phone(browser, live_server) -> None:
    context = await browser.new_context(viewport={"width": 390, "height": 844})
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        await sign_up(page, live_server)
        await enroll(page, live_server)
        await finish_survey_via_api(page)
        await _open_profile(page, live_server)
        assert await page.evaluate("() => document.documentElement.scrollWidth") <= 390
        assert errors == []
    finally:
        await context.close()


# --- аудит срезов 74–75 ---


async def _ready(page, live_server) -> None:
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    await _open_profile(page, live_server)


async def _pick_from_list(page, index: int = 5):
    await page.locator(".kg-list summary").click()
    target = page.locator("#kg-list button[data-node]").nth(index)
    await target.click()
    return target


async def test_double_click_switches_once(page, live_server) -> None:
    await _ready(page, live_server)
    await _pick_from_list(page)
    switches: list[str] = []
    page.on("request", lambda rq: switches.append(rq.url) if rq.url.endswith("/lesson/switch") else None)

    await page.locator("#kg-card").get_by_role("button", name="Перейти к занятию").dblclick()
    await page.wait_for_url(f"{live_server}/lesson/mlcourse")

    assert len(switches) == 1


async def test_pending_task_asks_before_switch(page, live_server) -> None:
    await _ready(page, live_server)
    await page.evaluate("() => fetch('/api/courses/mlcourse/lesson/task', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})")
    await _pick_from_list(page)

    await page.locator("#kg-card").get_by_role("button", name="Перейти к занятию").click()
    await expect(page.locator("#confirm-title")).to_have_text("Перейти к другому занятию?")


async def test_jittery_tap_opens_card(page, live_server) -> None:
    await _ready(page, live_server)
    # Точка текущего занятия — в центре после «К текущему».
    await page.locator("#kg-current").click()
    await page.locator("#kg-card").get_by_role("button", name="Закрыть").click()
    box = await page.locator("#kg-canvas").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 2, y + 1)
    await page.mouse.up()

    await expect(page.locator("#kg-card")).to_be_visible()


async def test_graph_sits_right_under_course_card(page, live_server) -> None:
    await _ready(page, live_server)
    learning = await page.locator(".learning").bounding_box()
    graph = await page.locator("#knowledge").bounding_box()

    assert graph["y"] - (learning["y"] + learning["height"]) < 40
