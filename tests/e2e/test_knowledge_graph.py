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
