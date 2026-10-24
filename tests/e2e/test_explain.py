"""E2E «Объясни новичку»: сессия, наставник, отчёт (срез 91)."""

import pytest

from helpers import COURSE, close_module, enroll, finish_survey_via_api, sign_up

pytestmark = pytest.mark.e2e


async def _send(page, text: str, expected: int) -> None:
    await page.fill("#explain-input", text)
    await page.press("#explain-input", "Enter")
    await page.wait_for_function(
        "n => document.querySelectorAll('#explain-log .msg:not(.pending)').length >= n", arg=expected
    )
    await page.wait_for_selector("#explain[aria-busy=false]")


async def test_explain_session_flow(page, lessons_server, tmp_path) -> None:
    await sign_up(page, lessons_server)
    await enroll(page, lessons_server)
    await finish_survey_via_api(page)
    close_module(tmp_path / "users", 1)

    await page.goto(f"{lessons_server}/lesson/{COURSE}/explain?topic=1")
    await page.wait_for_selector("#explain[aria-busy=false]")
    assert await page.locator("#explain-privacy, .explain-privacy").count() == 1
    await page.click("#explain-modules button")
    await page.wait_for_selector("#explain-chat:not([hidden])")
    first = page.locator("#explain-log .msg").first
    assert "🐣 Новичок" in await first.inner_text()
    assert "Ты объясняешь модуль 1" in await page.inner_text("#explain-lead")

    await _send(page, "Series — это один столбец с индексом.", 3)
    await _send(page, "А DataFrame — таблица из таких столбцов.", 5)
    assert "из 3 ключевых идей" in await page.inner_text("#explain-progress-label")

    await page.click("#explain-mentor")
    await page.wait_for_function(
        "() => document.querySelector('#explain-mentor').textContent.includes('Вернуться')"
    )
    await _send(page, "Не понимаю, чем loc отличается от iloc.", 7)
    last = page.locator("#explain-log .msg").last
    assert "🧑‍🏫 Наставник" in await last.inner_text()
    await page.click("#explain-mentor")
    await page.wait_for_function(
        "() => document.querySelector('#explain-mentor').textContent.includes('Позвать')"
    )

    await page.click("#explain-finish")
    await page.click("#confirm-submit")
    await page.wait_for_selector("#explain-report:not([hidden])")
    report = await page.inner_text("#explain-report")
    assert "Обязательных идей объяснено верно: 0 из 3" in report
    assert "Повторить" in report
    assert "звал наставника" in report
    assert await page.locator("#explain-form").is_hidden()

    await page.goto(f"{lessons_server}/lesson/{COURSE}/explain")
    await page.wait_for_selector("#explain[aria-busy=false]")
    assert await page.locator("#explain-history li").count() == 1
    assert page.errors == []
