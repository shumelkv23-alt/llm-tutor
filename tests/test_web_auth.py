"""Вход, регистрация, сессии и защита страниц (Срез 38)."""

import logging
import re

import httpx
import pytest

from llm_tutor.web import accounts
from llm_tutor.web.accounts import RESET_TOKEN_INVALID
from llm_tutor.web.app import create_app
from llm_tutor.web.security import SESSION_COOKIE, safe_next
from web_fakes import FailingMailer

ANN = {"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"}


async def _register(web, **overrides) -> httpx.Response:
    return await web.post("/api/auth/register", json={**ANN, **overrides})


async def test_register_logs_in(web) -> None:
    response = await _register(web)

    assert response.status_code == 201
    assert response.json()["user"] == {"email": "ann@example.com", "name": "Аня", "initial": "А"}
    me = await web.get("/api/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "ann@example.com"


async def test_session_cookie_flags(web) -> None:
    response = await _register(web)

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie  # COOKIE_SECURE=false по умолчанию


async def test_secure_cookie_when_configured(web_settings, web_llm) -> None:
    app = create_app(web_settings.model_copy(update={"cookie_secure": True}), client=web_llm)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        response = await client.post("/api/auth/register", json=ANN)

    assert "Secure" in response.headers["set-cookie"]


async def test_register_errors_name_the_field(web) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})

    duplicate = await _register(web, email="ANN@example.com")
    weak = await _register(web, email="bob@example.com", password="123")

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["field"] == "email"
    assert weak.status_code == 422
    assert weak.json()["detail"]["field"] == "password"


async def test_malformed_body_is_a_field_error_not_a_stack(web) -> None:
    response = await web.post("/api/auth/register", json={"email": "ann@example.com"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"]
    assert detail["field"] in {"name", "password"}


async def test_registration_can_be_closed(web_settings, web_llm) -> None:
    app = create_app(web_settings.model_copy(update={"registration_open": False}), client=web_llm)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/auth/register", json=ANN)
        page = await client.get("/register")

    assert response.status_code == 403
    assert "закрыта" in page.text


async def test_login_and_logout(web) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    assert (await web.get("/api/me")).status_code == 401

    login = await web.post(
        "/api/auth/login", json={"email": " ANN@example.com ", "password": ANN["password"]}
    )

    assert login.status_code == 200
    assert (await web.get("/api/me")).status_code == 200


async def test_logout_kills_session_on_server(web, web_app) -> None:
    """Выход удаляет сессию в БД: старый cookie больше не пускает."""
    await _register(web)
    token = web.cookies[SESSION_COOKIE]

    await web.post("/api/auth/logout", json={})

    web.cookies.set(SESSION_COOKIE, token)
    assert (await web.get("/api/me")).status_code == 401


async def test_wrong_password(web) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})

    response = await web.post("/api/auth/login", json={"email": ANN["email"], "password": "не тот"})

    assert response.status_code == 401
    assert response.json()["detail"]["message"] == "Неверный email или пароль."
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


async def test_login_rate_limit(web, web_app) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    clock = {"now": 1000.0}
    web_app.state.login_limiter.clock = lambda: clock["now"]
    wrong = {"email": ANN["email"], "password": "не тот"}

    for _ in range(5):
        assert (await web.post("/api/auth/login", json=wrong)).status_code == 401
    # Шестая попытка не проверяется даже с верным паролем.
    blocked = await web.post("/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]})
    assert blocked.status_code == 429

    clock["now"] += 15 * 60 + 1
    allowed = await web.post("/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]})
    assert allowed.status_code == 200


async def test_rate_limit_counts_email_case_insensitively(web, web_app) -> None:
    web_app.state.login_limiter.clock = lambda: 1000.0
    for variant in ("a@x.io", "A@x.io", " a@X.io", "a@x.IO", "A@X.IO"):
        await web.post("/api/auth/login", json={"email": variant, "password": "не тот"})

    assert (await web.post("/api/auth/login", json={"email": "a@x.io", "password": "x"})).status_code == 429


async def test_expired_session(web, web_app) -> None:
    await _register(web)
    web_app.state.accounts.execute("UPDATE web_sessions SET expires_at = 0")
    web_app.state.accounts.commit()

    assert (await web.get("/api/me")).status_code == 401
    page = await web.get("/profile")
    assert page.status_code == 303


async def test_rename_and_change_password(web) -> None:
    await _register(web)

    renamed = await web.post("/api/me", json={"name": "Анна"})
    wrong = await web.post("/api/me/password", json={"current": "не тот", "new": "новый-пароль"})
    changed = await web.post(
        "/api/me/password", json={"current": ANN["password"], "new": "новый-пароль"}
    )

    assert renamed.json()["user"]["name"] == "Анна"
    assert wrong.status_code == 422
    assert wrong.json()["detail"]["field"] == "current"
    assert changed.status_code == 200
    # Текущая сессия переживает смену пароля.
    assert (await web.get("/api/me")).status_code == 200
    await web.post("/api/auth/logout", json={})
    relogin = await web.post("/api/auth/login", json={"email": ANN["email"], "password": "новый-пароль"})
    assert relogin.status_code == 200


# --- CSRF: только JSON и только со своего сайта ---


async def test_form_post_is_rejected(web) -> None:
    response = await web.post("/api/auth/login", data={"email": "a@b.io", "password": "x"})

    assert response.status_code == 415


async def test_post_without_body_is_rejected(web) -> None:
    assert (await web.post("/api/auth/logout")).status_code == 415


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://testserver.evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
async def test_cross_site_post_is_rejected(web, headers: dict) -> None:
    response = await web.post("/api/auth/register", json=ANN, headers=headers)

    assert response.status_code == 403
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


async def test_same_origin_post_is_accepted(web) -> None:
    response = await web.post(
        "/api/auth/register",
        json=ANN,
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 201


async def test_get_api_is_not_blocked_by_content_type(web) -> None:
    assert (await web.get("/api/me")).status_code == 401


# --- Страницы ---


@pytest.mark.parametrize("path", ["/courses", "/lesson", "/lesson/mlcourse", "/profile"])
async def test_private_pages_redirect_guest_to_login(web, path: str) -> None:
    response = await web.get(path)

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path.replace('/', '%2F')}"


async def test_home_is_open_to_guest(web) -> None:
    response = await web.get("/")

    assert response.status_code == 200
    assert 'href="/login"' in response.text


async def test_private_page_shows_user_after_login(web) -> None:
    await _register(web)

    response = await web.get("/profile")

    assert response.status_code == 200
    assert "ann@example.com" in response.text
    assert 'id="logout"' in response.text


async def test_user_name_is_escaped(web) -> None:
    await _register(web, name="<img src=x onerror=alert(1)>")

    html = (await web.get("/profile")).text

    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


@pytest.mark.parametrize("path", ["/login", "/register"])
async def test_auth_pages_are_open_to_guest(web, path: str) -> None:
    response = await web.get(path)

    assert response.status_code == 200
    assert "<form" in response.text


@pytest.mark.parametrize("path", ["/login", "/register"])
async def test_auth_pages_send_logged_in_user_on(web, path: str) -> None:
    await _register(web)

    response = await web.get(f"{path}?next=/profile")

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "evil.example",
        "",
        # Браузер выбрасывает из URL табы и переводы строк: «/\t/x» станет «//x».
        "/\t/evil.example",
        "/\n/evil.example",
        " /profile",
    ],
)
def test_safe_next_rejects_foreign_targets(value: str) -> None:
    assert safe_next(value) == "/"


@pytest.mark.parametrize("value", ["/", "/profile", "/lesson/mlcourse?tab=code"])
def test_safe_next_keeps_own_paths(value: str) -> None:
    assert safe_next(value) == value


async def test_login_page_sanitizes_next(web) -> None:
    html = (await web.get("/login?next=//evil.example")).text

    assert re.search(r'data-next="/"', html)
    assert "evil.example" not in html


async def test_logged_in_user_with_foreign_next_goes_home(web) -> None:
    await _register(web)

    response = await web.get("/login?next=https://evil.example")

    assert response.headers["location"] == "/"


async def test_sliding_session_refreshes_cookie(web, web_app) -> None:
    """Сервер продлил сессию — браузер получает новый cookie на полный срок."""
    from llm_tutor.web import accounts

    await _register(web)
    db = web_app.state.accounts
    db.execute("UPDATE web_sessions SET expires_at = expires_at - ?", (accounts.SESSION_TTL * 0.6,))
    db.commit()

    response = await web.get("/api/me")

    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "")
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert f"Max-Age={int(accounts.SESSION_TTL)}" in cookie
    assert "set-cookie" not in (await web.get("/api/me")).headers


async def test_password_change_is_rate_limited(web, web_app) -> None:
    await _register(web)
    web_app.state.login_limiter.clock = lambda: 1000.0

    for _ in range(5):
        wrong = await web.post("/api/me/password", json={"current": "не тот", "new": "новый-пароль"})
        assert wrong.status_code == 422
    blocked = await web.post("/api/me/password", json={"current": ANN["password"], "new": "новый-пароль"})

    assert blocked.status_code == 429


async def test_login_does_not_block_the_event_loop(web, monkeypatch) -> None:
    """Сверка пароля идёт в потоке: параллельные входы не останавливают запросы.

    Подменяем scrypt на предсказуемую паузу: настоящий scrypt на 12 параллельных
    входах сам по себе перегружает CPU раннера, и порог по времени становится
    плавающим. С паузой 0.15 с синхронный вызов задержал бы healthz на ~1.8 с,
    а вынесенный в поток — нет.
    """
    import asyncio
    import time

    def slow_verify(password: str, stored: str) -> bool:
        time.sleep(0.15)
        return False

    # Неизвестный email не должен тратить настоящий scrypt на фиктивный хеш:
    # иначе первый вход считает его прямо в цикле событий и портит замер.
    monkeypatch.setattr(accounts, "credentials", lambda db, email: (None, "dummy"))
    monkeypatch.setattr(accounts, "verify_password", slow_verify)

    async def attempt(index: int):
        return await web.post("/api/auth/login", json={"email": f"x{index}@e.io", "password": "пароль-123"})

    started = time.perf_counter()
    logins = [asyncio.create_task(attempt(i)) for i in range(12)]
    await asyncio.sleep(0.01)
    health = await web.get("/healthz")
    health_latency = time.perf_counter() - started
    # healthz ответил, пока медленные входы ещё считаются в потоках. Если бы
    # сверка шла в цикле событий, к этому моменту они были бы уже завершены.
    still_running = any(not task.done() for task in logins)
    await asyncio.gather(*logins)

    assert health.status_code == 200
    assert health_latency < 0.5
    assert still_running


# --- Восстановление пароля по почте: API (Срез 72) ---

NEW_PASSWORD = "совсем-новый-пароль"


async def _ask_for_letter(web, email: str = ANN["email"]) -> httpx.Response:
    return await web.post("/api/auth/forgot-password", json={"email": email})


async def test_forgot_password_sends_a_letter_with_a_link(web, web_mailer) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})

    response = await _ask_for_letter(web)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    to, subject, text = web_mailer.last()
    assert to == ANN["email"]
    assert "llm·tutor" in subject
    assert "Восстановление пароля" in subject
    assert "Аня" in text
    assert web_mailer.last_link().startswith("http://127.0.0.1:8000/reset-password?token=")


async def test_response_does_not_carry_the_token(web, web_mailer) -> None:
    """Секрет живёт в письме, а не в ответе: ответ одинаков для всех."""
    await _register(web)
    await web.post("/api/auth/logout", json={})

    body = (await _ask_for_letter(web)).text

    assert web_mailer.last_token() not in body
    assert "token" not in body


@pytest.mark.parametrize("email", ["nobody@example.com", "не-адрес", "", "   "])
async def test_forgot_password_answers_the_same_for_any_address(web, web_mailer, email: str) -> None:
    """Антиперечисление: по ответу нельзя понять, зарегистрирован ли email."""
    await _register(web)
    await web.post("/api/auth/logout", json={})

    response = await _ask_for_letter(web, email)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert web_mailer.sent == []


async def test_letter_goes_only_to_the_owner(web, web_mailer) -> None:
    await _register(web)  # ann@example.com
    await web.post("/api/auth/logout", json={})

    await _ask_for_letter(web, "bob@example.com")
    await _ask_for_letter(web)

    assert [to for to, _, _ in web_mailer.sent] == [ANN["email"]]


async def test_new_request_invalidates_the_old_link(web, web_mailer) -> None:
    """«Последняя заявка побеждает»: ссылка из старого письма больше не годится."""
    await _register(web)
    await web.post("/api/auth/logout", json={})
    await _ask_for_letter(web)
    stale = web_mailer.last_token()
    await _ask_for_letter(web)
    fresh = web_mailer.last_token()

    old = await web.post("/api/auth/reset-password", json={"token": stale, "password": NEW_PASSWORD})
    new = await web.post("/api/auth/reset-password", json={"token": fresh, "password": NEW_PASSWORD})

    assert old.status_code == 422
    assert old.json()["detail"]["field"] == "token"
    assert new.status_code == 200


async def test_forgot_password_is_rate_limited(web, web_app, web_mailer) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    web_app.state.reset_limiter.clock = lambda: 1000.0

    for _ in range(3):
        assert (await _ask_for_letter(web)).status_code == 200
    blocked = await _ask_for_letter(web)

    assert blocked.status_code == 429
    assert blocked.json()["detail"]["message"]
    # Письма, отправленные до блокировки, на месте.
    assert len(web_mailer.sent) == 3


async def test_reset_limit_does_not_block_logins(web, web_app) -> None:
    """Счётчики сброса и входа независимы: иначе заявки закрыли бы вход."""
    await _register(web)
    await web.post("/api/auth/logout", json={})
    web_app.state.reset_limiter.clock = lambda: 1000.0
    for _ in range(4):
        await _ask_for_letter(web)
    assert (await _ask_for_letter(web)).status_code == 429

    login = await web.post(
        "/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]}
    )

    assert login.status_code == 200


@pytest.mark.parametrize("path", ["/api/auth/forgot-password", "/api/auth/reset-password"])
async def test_reset_api_is_guarded_like_the_rest(web, path: str) -> None:
    """Общий api_guard: форму с чужого сайта не примут, и «забыть» его нельзя."""
    as_form = await web.post(path, data={"email": ANN["email"], "token": "x", "password": "y"})
    foreign = await web.post(
        path,
        json={"email": ANN["email"], "token": "x", "password": "y"},
        headers={"Origin": "https://evil.example"},
    )

    assert as_form.status_code == 415
    assert foreign.status_code == 403


async def test_reset_password_by_link(web, web_mailer) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    await _ask_for_letter(web)

    response = await web.post(
        "/api/auth/reset-password",
        json={"token": web_mailer.last_token(), "password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    # Старый пароль не подходит, новый — пускает.
    old = await web.post("/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]})
    fresh = await web.post("/api/auth/login", json={"email": ANN["email"], "password": NEW_PASSWORD})
    assert old.status_code == 401
    assert fresh.status_code == 200


async def test_reset_closes_other_sessions(web, web_app, web_mailer) -> None:
    """Чужой вход (например, угнанный) закрывается вместе со сменой пароля."""
    await _register(web)
    await web.post("/api/auth/logout", json={})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web_app), base_url="http://testserver"
    ) as second:
        logged = await second.post(
            "/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]}
        )
        assert logged.status_code == 200
        await _ask_for_letter(web)

        reset = await web.post(
            "/api/auth/reset-password",
            json={"token": web_mailer.last_token(), "password": NEW_PASSWORD},
        )

        assert reset.status_code == 200
        assert (await second.get("/api/me")).status_code == 401


async def test_reset_unblocks_login_after_failed_attempts(web, web_app, web_mailer) -> None:
    """Забыл пароль, набрал неверный пять раз — сброс снимает блокировку входа."""
    await _register(web)
    await web.post("/api/auth/logout", json={})
    web_app.state.login_limiter.clock = lambda: 1000.0
    for _ in range(5):
        await web.post("/api/auth/login", json={"email": ANN["email"], "password": "не тот"})
    blocked = await web.post(
        "/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]}
    )

    await _ask_for_letter(web)
    reset = await web.post(
        "/api/auth/reset-password",
        json={"token": web_mailer.last_token(), "password": NEW_PASSWORD},
    )
    login = await web.post(
        "/api/auth/login", json={"email": ANN["email"], "password": NEW_PASSWORD}
    )

    assert blocked.status_code == 429
    assert reset.status_code == 200
    assert login.status_code == 200


async def test_short_password_does_not_burn_the_token(web, web_mailer) -> None:
    """Опечатку в пароле ученик исправляет на той же странице — ссылка цела."""
    await _register(web)
    await web.post("/api/auth/logout", json={})
    await _ask_for_letter(web)
    token = web_mailer.last_token()

    weak = await web.post("/api/auth/reset-password", json={"token": token, "password": "123"})
    good = await web.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})

    assert weak.status_code == 422
    assert weak.json()["detail"]["field"] == "password"
    assert good.status_code == 200


async def test_used_link_does_not_work_twice(web, web_mailer) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    await _ask_for_letter(web)
    token = web_mailer.last_token()
    first = await web.post(
        "/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )

    again = await web.post(
        "/api/auth/reset-password", json={"token": token, "password": "ещё-один-пароль"}
    )

    assert first.status_code == 200
    assert again.status_code == 422
    assert again.json()["detail"]["field"] == "token"
    assert again.json()["detail"]["message"] == RESET_TOKEN_INVALID


@pytest.mark.parametrize("token", ["", "мусор", "a" * 500])
async def test_garbage_link_is_rejected(web, token: str) -> None:
    response = await web.post(
        "/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "token"


async def test_expired_link_is_rejected(web, web_app, web_mailer) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    await _ask_for_letter(web)
    web_app.state.accounts.execute("UPDATE password_resets SET expires_at = 0")
    web_app.state.accounts.commit()

    response = await web.post(
        "/api/auth/reset-password",
        json={"token": web_mailer.last_token(), "password": NEW_PASSWORD},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "token"


async def test_broken_mail_does_not_break_the_request(web_settings, web_llm) -> None:
    """Письмо уходит фоном: отказ почты не должен ломать ответ ученику."""
    app = create_app(web_settings, client=web_llm, mailer=FailingMailer())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.post("/api/auth/register", json=ANN)
        await client.post("/api/auth/logout", json={})
        response = await client.post("/api/auth/forgot-password", json={"email": ANN["email"]})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_reset_flow_does_not_log_secrets(web, web_mailer, caplog) -> None:
    """Токен и новый пароль не попадают в журнал (почта настроена — письмо не печатается)."""
    await _register(web)
    await web.post("/api/auth/logout", json={})
    with caplog.at_level(logging.DEBUG):
        await _ask_for_letter(web)
        token = web_mailer.last_token()
        await web.post(
            "/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
        )

    assert token not in caplog.text
    assert NEW_PASSWORD not in caplog.text


# --- Восстановление пароля: страницы (Срез 73) ---


async def test_forgot_password_page_is_open_to_guest(web) -> None:
    response = await web.get("/forgot-password")

    assert response.status_code == 200
    assert 'data-endpoint="/api/auth/forgot-password"' in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_forgot_password_page_sends_logged_in_user_to_profile(web) -> None:
    await _register(web)

    response = await web.get("/forgot-password")

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


async def test_login_page_links_to_recovery(web) -> None:
    assert 'href="/forgot-password"' in (await web.get("/login")).text


async def test_forgot_password_warns_when_mail_is_off(web) -> None:
    """Почта не настроена — ученик должен знать, что письма не будет."""
    assert "не настроена" in (await web.get("/forgot-password")).text


async def test_forgot_password_is_quiet_when_mail_works(web_settings, web_llm) -> None:
    from pydantic import SecretStr

    configured = web_settings.model_copy(
        update={"smtp_user": "tutor@example.com", "smtp_password": SecretStr("пароль-приложения")}
    )
    app = create_app(configured, client=web_llm)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        page = await client.get("/forgot-password")

    assert "не настроена" not in page.text


async def test_reset_page_shows_the_form_for_a_live_link(web, web_mailer) -> None:
    await _register(web)
    await web.post("/api/auth/logout", json={})
    await _ask_for_letter(web)
    token = web_mailer.last_token()

    response = await web.get(f"/reset-password?token={token}")

    assert response.status_code == 200
    assert f'name="token" value="{token}"' in response.text
    assert ANN["email"] in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("query", ["", "?token=", "?token=мусор", "?token=" + "a" * 500])
async def test_reset_page_refuses_a_dead_link(web, query: str) -> None:
    response = await web.get(f"/reset-password{query}")

    assert response.status_code == 200
    assert "Ссылка недействительна" in response.text
    assert 'href="/forgot-password"' in response.text
    assert 'name="token"' not in response.text


async def test_reset_page_refuses_a_used_link(web, web_mailer) -> None:
    """По ссылке уже сменили пароль — второй раз страница форму не покажет."""
    await _register(web)
    await web.post("/api/auth/logout", json={})
    await _ask_for_letter(web)
    token = web_mailer.last_token()
    await web.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})

    response = await web.get(f"/reset-password?token={token}")

    assert "Ссылка недействительна" in response.text
    assert 'name="token"' not in response.text


async def test_reset_page_does_not_reflect_a_hostile_token(web) -> None:
    """Негодный токен в разметку не попадает вообще: страница его не пересказывает."""
    response = await web.get("/reset-password", params={"token": '"><script>alert(1)</script>'})

    assert "<script>" not in response.text
    assert "alert(1)" not in response.text


async def test_reset_page_does_not_require_a_session(web, web_mailer) -> None:
    """Ссылка — самостоятельное доказательство: вошедшего она тоже пускает."""
    await _register(web)
    await _ask_for_letter(web)
    token = web_mailer.last_token()

    response = await web.get(f"/reset-password?token={token}")

    assert response.status_code == 200
    assert f'name="token" value="{token}"' in response.text
