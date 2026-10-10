"""Общая оболочка в браузере: подтверждение (аудит среза 37)."""

import pytest

pytestmark = pytest.mark.e2e


async def test_second_confirm_does_not_inherit_first(page, live_server) -> None:
    await page.goto(f"{live_server}/")
    await page.wait_for_function("() => window.App !== undefined")
    await page.evaluate(
        """() => {
            window.results = [];
            App.confirm('Первое', 'а').then((v) => results.push(['first', v]));
            App.confirm('Второе', 'б').then((v) => results.push(['second', v]));
        }"""
    )
    assert await page.text_content("#confirm-title") == "Второе"
    await page.click("#confirm-submit")
    await page.wait_for_function("() => window.results.length === 2")

    assert await page.evaluate("() => window.results") == [["first", False], ["second", True]]
