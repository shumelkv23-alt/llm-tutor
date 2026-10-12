"""Админка в браузере: сброс пароля показывается один раз и не остаётся в DOM (срез 66)."""

import httpx
import pytest

from llm_tutor.web import accounts

pytestmark = pytest.mark.e2e

PASSWORD = "секретный-пароль"
ADMIN_PASSWORD = "пароль-админа-1"


def _register(base: str, email: str, name: str, password: str) -> httpx.Client:
    client = httpx.Client(base_url=base)
    response = client.post("/api/auth/register", json={"email": email, "name": name, "password": password})
    assert response.status_code == 201
    return client


async def _admin_page(page, base: str, tmp_path):
    """Админ вошёл в браузер; возвращает клиент ученика и его id."""
    student = _register(base, "student@example.com", "Ученик", PASSWORD)
    _register(base, "root@example.com", "Владелец", ADMIN_PASSWORD).close()
    db = accounts.open_accounts(str(tmp_path / "accounts.sqlite3"))
    try:
        accounts.set_admin(db, accounts.find_user(db, "root@example.com").id, True)
        student_id = accounts.find_user(db, "student@example.com").id
    finally:
        db.close()
    await page.goto(f"{base}/login?next=/admin/students/{student_id}")
    await page.fill("input[name=email]", "root@example.com")
    await page.fill("input[name=password]", ADMIN_PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_url(f"{base}/admin/students/{student_id}")
    return student, student_id


async def _reset(page) -> str:
    await page.click("button[data-action=reset-password]")
    await page.click("#confirm-submit")
    await page.wait_for_selector("#password-dialog[open]")
    return await page.input_value("#password-value")


async def test_reset_password_is_shown_once_and_leaves_the_dom(page, live_server, tmp_path) -> None:
    student, _ = await _admin_page(page, live_server, tmp_path)
    assert student.get("/api/me").status_code == 200

    password = await _reset(page)

    assert len(password) >= 16
    assert await page.text_content("#session-count") == "0"
    assert student.get("/api/me").status_code == 401  # сессия ученика закрыта
    with httpx.Client(base_url=live_server) as fresh:
        login = fresh.post("/api/auth/login", json={"email": "student@example.com", "password": password})
        assert login.status_code == 200

    await page.click("#password-dialog button[value=close]")
    await page.wait_for_selector("#password-dialog", state="hidden")

    # Очистка не ждёт события close: пароль уходит из поля сразу.
    assert await page.input_value("#password-value") == ""
    assert password not in await page.content()
    assert password not in await page.evaluate("() => JSON.stringify([localStorage, sessionStorage])")
    assert page.errors == []
    await page.reload()
    assert password not in await page.content()
    assert "Сброс пароля" in await page.text_content("body")  # запись журнала
    student.close()


async def test_escape_also_clears_the_password(page, live_server, tmp_path) -> None:
    student, _ = await _admin_page(page, live_server, tmp_path)

    password = await _reset(page)
    await page.keyboard.press("Escape")
    # Событие close у dialog приходит на такт позже, чем он скрывается.
    await page.wait_for_function("() => document.getElementById('password-value').value === ''")

    assert password not in await page.content()
    student.close()


async def test_cancelling_the_confirmation_changes_nothing(page, live_server, tmp_path) -> None:
    student, _ = await _admin_page(page, live_server, tmp_path)

    await page.click("button[data-action=reset-password]")
    await page.click("#confirm-dialog button[value=cancel]")

    assert await page.locator("#password-dialog[open]").count() == 0
    assert student.get("/api/me").status_code == 200
    student.close()


async def test_close_sessions_updates_the_counter(page, live_server, tmp_path) -> None:
    student, _ = await _admin_page(page, live_server, tmp_path)

    await page.click("button[data-action=close-sessions]")
    await page.click("#confirm-submit")
    await page.wait_for_function("() => document.getElementById('session-count').textContent === '0'")

    assert student.get("/api/me").status_code == 401
    student.close()


async def test_actions_fit_a_phone(page, live_server, tmp_path) -> None:
    await page.set_viewport_size({"width": 320, "height": 700})
    student, _ = await _admin_page(page, live_server, tmp_path)

    await _reset(page)

    box = await page.locator("#password-dialog").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 320
    assert await page.evaluate("() => document.documentElement.scrollWidth") <= 320
    student.close()
