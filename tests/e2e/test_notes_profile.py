"""Заметка из выделенного фрагмента появляется в профиле (e2e)."""

import pytest
from playwright.async_api import expect

from helpers import enroll, finish_survey_via_api, sign_up

pytestmark = pytest.mark.e2e


async def test_selection_note_appears_in_profile(page, live_server) -> None:
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)

    await page.click("#tab-btn-theory")
    await page.wait_for_selector("#tab-theory .theory-body p")
    quote = (await page.text_content("#tab-theory .theory-body p")).strip()
    assert quote
    await page.evaluate(
        """() => {
            const p = document.querySelector('#tab-theory .theory-body p');
            const range = document.createRange();
            range.selectNodeContents(p);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }"""
    )
    await page.wait_for_selector(".selection-bar:not([hidden])")
    await page.click('.selection-bar button:has-text("В заметку")')
    await expect(page.locator("#toast")).to_contain_text("Заметка сохранена")

    await page.goto(f"{live_server}/profile", wait_until="networkidle")
    await expect(page.locator(".note-list .note-row")).to_have_count(1)
    await expect(page.locator(".note-list")).to_contain_text(quote.split()[0])

    # Ссылка из заметки открывает занятие и прокручивает к её разделу.
    await page.click(".note-list .note-row .note-meta a")
    # Ждём конец перехода: lesson.js убирает ?node из адреса, когда состояние
    # загружено и раздел отрисован.
    await page.wait_for_function(
        "() => location.pathname.startsWith('/lesson/') && !location.search.includes('node=')"
    )
    assert await page.evaluate(
        "() => location.hash.startsWith('#section-')"
        " && Boolean(document.getElementById(location.hash.slice(1)))"
    )
    assert page.errors == []
