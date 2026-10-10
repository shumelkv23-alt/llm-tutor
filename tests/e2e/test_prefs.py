"""Настройки вида в браузере (Срез 48): свёрнутый сайдбар и тёмная тема.

Обе настройки ставит prefs.js в <head> до первой отрисовки — после
перезагрузки страница сразу в нужном виде, без мигания.
"""

import pytest
from helpers import enroll, sign_up
from playwright.async_api import expect

pytestmark = pytest.mark.e2e


async def _sidebar_width(page) -> float:
    return await page.evaluate(
        "() => document.querySelector('#sidebar').getBoundingClientRect().width"
    )


async def test_sidebar_toggle_persists(page, live_server) -> None:
    await sign_up(page, live_server)
    root = page.locator("html")
    toggle = page.locator("#sidebar-toggle")

    await expect(root).not_to_have_attribute("data-sidebar", "collapsed")
    wide = await _sidebar_width(page)
    await toggle.click()
    await expect(root).to_have_attribute("data-sidebar", "collapsed")
    await expect(toggle).to_have_attribute("aria-expanded", "false")
    assert await _sidebar_width(page) < wide

    await page.reload()
    # Атрибут стоит до DOMContentLoaded: его ставит prefs.js в <head>.
    await expect(root).to_have_attribute("data-sidebar", "collapsed")
    await expect(toggle).to_have_attribute("aria-expanded", "false")
    # Подписи скрыты визуально, но остаются доступным именем ссылок.
    await expect(page.get_by_role("link", name="Мои курсы")).to_be_visible()

    await toggle.click()
    await page.reload()
    await expect(root).not_to_have_attribute("data-sidebar", "collapsed")


async def test_lesson_collapses_sidebar_by_default(page, live_server) -> None:
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await expect(page.locator("html")).to_have_attribute("data-sidebar", "collapsed")

    await page.locator("#sidebar-toggle").click()
    await page.goto(f"{live_server}/courses")
    await page.go_back()
    # Выбор ученика сильнее умолчания страницы.
    await expect(page.locator("html")).not_to_have_attribute(
        "data-sidebar", "collapsed"
    )


async def test_sidebar_toggle_hidden_on_phone(browser, live_server) -> None:
    context = await browser.new_context(viewport={"width": 390, "height": 844})
    page = await context.new_page()
    try:
        await sign_up(page, live_server)
        await expect(page.locator("#sidebar-toggle")).to_be_hidden()
    finally:
        await context.close()


async def test_theme_picker_persists(page, live_server) -> None:
    await sign_up(page, live_server)
    await page.goto(f"{live_server}/profile")
    root = page.locator("html")
    dark = page.locator("[data-theme-choice=dark]")
    system = page.locator("[data-theme-choice=system]")

    await expect(system).to_have_attribute("aria-checked", "true")
    light_bg = await page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor"
    )
    await dark.click()
    await expect(root).to_have_attribute("data-theme", "dark")
    await expect(dark).to_have_attribute("aria-checked", "true")
    assert (
        await page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        != light_bg
    )

    await page.goto(f"{live_server}/")
    await expect(root).to_have_attribute("data-theme", "dark")

    await page.goto(f"{live_server}/profile")
    await system.click()
    await expect(root).not_to_have_attribute("data-theme", "dark")
    await page.reload()
    await expect(page.locator("[data-theme-choice=system]")).to_have_attribute(
        "aria-checked", "true"
    )


async def test_system_dark_theme(browser, live_server) -> None:
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900}, color_scheme="dark"
    )
    page = await context.new_page()
    try:
        await sign_up(page, live_server)
        dark_bg = await page.evaluate(
            "() => getComputedStyle(document.body).backgroundColor"
        )
        await page.evaluate("() => localStorage.setItem('llmTutor.theme', 'light')")
        await page.reload()
        await expect(page.locator("html")).to_have_attribute("data-theme", "light")
        assert (
            await page.evaluate("() => getComputedStyle(document.body).backgroundColor")
            != dark_bg
        )
    finally:
        await context.close()
