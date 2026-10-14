"""Забыл пароль: письмо, ссылка, новый пароль, вход — в браузере (срез 74)."""

import httpx
import pytest

pytestmark = pytest.mark.e2e

EMAIL = "ann@example.com"
PASSWORD = "секретный-пароль"
NEW_PASSWORD = "совсем-новый-пароль"


def _register(base: str) -> None:
    with httpx.Client(base_url=base) as client:
        response = client.post(
            "/api/auth/register", json={"email": EMAIL, "name": "Аня", "password": PASSWORD}
        )
    assert response.status_code == 201


def _login(base: str, password: str) -> int:
    with httpx.Client(base_url=base) as client:
        response = client.post("/api/auth/login", json={"email": EMAIL, "password": password})
    return response.status_code


async def test_the_whole_way_from_forgotten_password_to_a_new_one(page, mail_server) -> None:
    base, post = mail_server
    _register(base)

    # 1. Со страницы входа — на восстановление.
    await page.goto(f"{base}/login")
    await page.click('a[href="/forgot-password"]')
    await page.wait_for_url(f"{base}/forgot-password")

    # 2. Заявка: страница отвечает «проверьте почту», поля исчезают.
    await page.fill("input[name=email]", EMAIL)
    await page.click("button[type=submit]")
    await page.wait_for_selector("[data-success]:not([hidden])")
    assert await page.locator("form.auth-form").is_hidden()

    # 3. Письмо пришло на этот же адрес и ведёт на этот же сервер.
    link = post.link()
    assert link.startswith(f"{base}/reset-password?token=")

    # 4. По ссылке — форма нового пароля.
    await page.goto(link)
    assert await page.locator("input[name=password]").is_visible()
    await page.fill("input[name=password]", NEW_PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_selector("[data-success]:not([hidden])")
    assert "Пароль изменён" in await page.text_content("body")

    # 5. Старый пароль не пускает, новый — пускает.
    assert _login(base, PASSWORD) == 401
    assert _login(base, NEW_PASSWORD) == 200

    # 6. Ссылка одноразовая.
    await page.goto(link)
    assert "Ссылка недействительна" in await page.text_content("body")
    assert page.errors == []


async def test_a_stranger_address_gets_no_letter(page, mail_server) -> None:
    """Антиперечисление вживую: ответ тот же, а письма нет."""
    base, post = mail_server
    _register(base)

    await page.goto(f"{base}/forgot-password")
    await page.fill("input[name=email]", "nobody@example.com")
    await page.click("button[type=submit]")
    await page.wait_for_selector("[data-success]:not([hidden])")

    assert not list(post.outbox.glob("letter-*.txt"))
    assert page.errors == []


async def test_recovery_fits_a_phone(page, mail_server) -> None:
    await page.set_viewport_size({"width": 320, "height": 700})
    base, _ = mail_server

    await page.goto(f"{base}/forgot-password")

    assert await page.evaluate("() => document.documentElement.scrollWidth") <= 320
    assert page.errors == []
