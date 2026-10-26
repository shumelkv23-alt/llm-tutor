"""Админка: роль, доступ, меню, CLI (Срез 63)."""

import sqlite3

import httpx
import pytest

from llm_tutor.web import accounts, api_admin, security
from llm_tutor.web.cli import main as cli_main

ANN = {"email": "ann@example.com", "name": "Аня", "password": "секретный-пароль"}
BOB = {"email": "bob@example.com", "name": "Боб", "password": "другой-пароль-1"}
# Имя не должно быть подстрокой слов интерфейса («Админка»): иначе проверки «имя на странице» вхолостую.
ADMIN = {"email": "root@example.com", "name": "Хозяин", "password": "пароль-админа-1"}


def _set_admin(web_app, email: str, value: bool = True) -> None:
    db = web_app.state.accounts
    user = accounts.find_user(db, email)
    assert user is not None
    accounts.set_admin(db, user.id, value)


async def _sign_up(client, person: dict) -> None:
    response = await client.post("/api/auth/register", json=person)
    assert response.status_code == 201


@pytest.fixture
async def admin(web, web_app):
    """Клиент вошедшего админа."""
    await _sign_up(web, ADMIN)
    _set_admin(web_app, ADMIN["email"])
    return web


# --- Доступ ---


async def test_guest_is_sent_to_login(web) -> None:
    response = await web.get("/admin")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fadmin"


async def test_guest_on_students_page_is_sent_to_login(web) -> None:
    response = await web.get("/admin/students")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")


async def test_plain_student_gets_404_like_an_unknown_page(student) -> None:
    unknown = await student.get("/no-such-page")

    for path in ("/admin", "/admin/students"):
        response = await student.get(path)
        assert response.status_code == 404
        # Тот же ответ, что у несуществующей страницы: раздел не выдаёт себя.
        assert response.json() == unknown.json()


async def test_admin_is_redirected_to_students(admin) -> None:
    response = await admin.get("/admin")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/students"
    assert (await admin.get("/admin/students")).status_code == 200


async def test_revoked_admin_loses_access_without_relogin(admin, web_app) -> None:
    assert (await admin.get("/admin/students")).status_code == 200

    _set_admin(web_app, ADMIN["email"], False)

    assert (await admin.get("/admin/students")).status_code == 404


async def test_admin_role_is_not_given_by_the_web(student) -> None:
    """Роль выдаёт только CLI: регистрация и профиль её не принимают."""
    response = await student.post("/api/me", json={"name": "Аня", "is_admin": True})

    assert response.status_code == 200
    assert (await student.get("/admin/students")).status_code == 404


def _admin_endpoints(web_app) -> list[tuple[str, str]]:
    """Все (метод, путь) админки из схемы приложения.

    Не ``app.routes``: включённые роутеры там лежат лениво и путей не отдают.
    """
    endpoints = [
        (method.upper(), path.replace("{user_id}", "1"))
        for path, methods in web_app.openapi()["paths"].items()
        if path.startswith(("/admin", "/api/admin"))
        for method in methods
    ]
    assert ("GET", "/admin/students") in endpoints
    return endpoints


async def _call(client, method: str, path: str):
    return await client.request(method, path, json=None if method == "GET" else {})


async def test_every_admin_route_hides_from_plain_student(student, web_app) -> None:
    """Перебор маршрутов: ни один путь админки не отвечает не-админу иначе, чем 404."""
    for method, path in _admin_endpoints(web_app):
        response = await _call(student, method, path)
        assert response.status_code == 404, (method, path)


async def test_every_admin_route_asks_guest_to_log_in(web, web_app) -> None:
    for method, path in _admin_endpoints(web_app):
        response = await _call(web, method, path)
        assert response.status_code in (303, 401), (method, path)


def test_api_router_is_guarded_as_a_whole() -> None:
    """Защита на роутере, а не в каждой функции: новый маршрут её не забудет."""
    guards = [dependency.dependency for dependency in api_admin.router.dependencies]

    assert security.admin_user in guards


# --- Последний вход ---


def _last_login(web_app, email: str):
    row = web_app.state.accounts.execute(
        "SELECT last_login_at FROM users WHERE email = ?", (email,)
    ).fetchone()
    return row[0]


async def test_register_and_login_record_last_login(web, web_app) -> None:
    await _sign_up(web, ANN)
    registered = _last_login(web_app, ANN["email"])
    assert registered is not None

    await web.post("/api/auth/logout", json={})
    response = await web.post(
        "/api/auth/login", json={"email": ANN["email"], "password": ANN["password"]}
    )

    assert response.status_code == 200
    assert _last_login(web_app, ANN["email"]) >= registered


async def test_failed_login_does_not_touch_last_login(web, web_app) -> None:
    await _sign_up(web, ANN)
    before = _last_login(web_app, ANN["email"])
    await web.post("/api/auth/logout", json={})

    response = await web.post("/api/auth/login", json={"email": ANN["email"], "password": "не-тот-пароль"})

    assert response.status_code == 401
    assert _last_login(web_app, ANN["email"]) == before


# --- Меню ---


async def test_admin_menu_item_only_for_admins(admin, web_app) -> None:
    page = (await admin.get("/admin/students")).text
    home = (await admin.get("/")).text

    assert 'data-nav="admin"' in page
    assert 'href="/admin"' in home


async def test_plain_student_and_guest_have_no_admin_menu_item(student, web_app) -> None:
    assert 'href="/admin"' not in (await student.get("/")).text
    transport = httpx.ASGITransport(app=web_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as guest:
        assert 'href="/admin"' not in (await guest.get("/")).text


# --- CLI ---


def _user(web_settings, person: dict = ANN):
    db = accounts.open_accounts(web_settings.accounts_db_path)
    user = accounts.create_user(db, **person)
    db.close()
    return user


def _is_admin(web_settings, email: str) -> bool:
    db = accounts.open_accounts(web_settings.accounts_db_path)
    try:
        user = accounts.find_user(db, email)
        return user is not None and user.is_admin
    finally:
        db.close()


def test_make_and_revoke_admin(web_settings) -> None:
    _user(web_settings)

    assert cli_main(["make-admin", "--email", "ANN@example.com"], settings=web_settings) == 0
    assert _is_admin(web_settings, "ann@example.com")
    # Повторный запуск ничего не ломает.
    assert cli_main(["make-admin", "--email", "ann@example.com"], settings=web_settings) == 0
    assert _is_admin(web_settings, "ann@example.com")

    assert cli_main(["revoke-admin", "--email", "ann@example.com"], settings=web_settings) == 0
    assert not _is_admin(web_settings, "ann@example.com")
    assert cli_main(["revoke-admin", "--email", "ann@example.com"], settings=web_settings) == 0


@pytest.mark.parametrize("command", ["make-admin", "revoke-admin"])
def test_admin_commands_unknown_email(web_settings, capsys, command: str) -> None:
    code = cli_main([command, "--email", "nobody@example.com"], settings=web_settings)

    assert code == 1
    assert "зарегистр" in capsys.readouterr().out


def test_make_admin_does_not_touch_other_users(web_settings) -> None:
    _user(web_settings)
    _user(web_settings, BOB)

    cli_main(["make-admin", "--email", "ann@example.com"], settings=web_settings)

    assert not _is_admin(web_settings, "bob@example.com")


def test_cli_leaves_database_usable(web_settings) -> None:
    _user(web_settings)
    cli_main(["make-admin", "--email", "ann@example.com"], settings=web_settings)

    conn = sqlite3.connect(web_settings.accounts_db_path)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()


# --- Снимок и список учеников (Срез 64) ---

import logging
import sqlite3 as _sqlite3
import time

from course_fixtures import WEB_T1
from llm_tutor.db import repos
from llm_tutor.schemas import Event
from llm_tutor.student import survey
from llm_tutor.web import admin as admin_data
from llm_tutor.web.courses import COURSES

COURSE = COURSES[0].id
BASE = f"/api/courses/{COURSE}/lesson"
PAGE_SIZE = 50


def _answers() -> list[int]:
    progress, given = survey.Progress(WEB_T1), []
    while progress.next_key() is not None:
        progress, given = progress.answer(1), [*given, 1]
    return given


def _client(web_app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app), base_url="http://testserver")


async def _learner(web_app, name: str, *, survey_done: bool = False, enroll: bool = True):
    """Ученик со своим клиентом: регистрация, запись на курс, при желании анкета."""
    client = _client(web_app)
    person = {"email": f"{name.lower()}@example.com", "name": name, "password": "секретный-пароль"}
    assert (await client.post("/api/auth/register", json=person)).status_code == 201
    user = accounts.find_user(web_app.state.accounts, person["email"])
    if enroll:
        assert (await client.post(f"/api/courses/{COURSE}/enroll", json={})).status_code == 200
    if survey_done:
        assert (await client.post(f"{BASE}/survey/finish", json={"answers": _answers()})).status_code == 200
    return client, user


def _db(web_app, user):
    return web_app.state.userdbs.open(user.id, COURSE)


def _close_lesson(web_app, user, node_id: str) -> None:
    conn = _db(web_app, user)
    conn.execute(
        "INSERT OR REPLACE INTO mastery (concept_id, alpha, beta, last_seen) VALUES (?, 60, 1, ?)",
        (node_id, time.time()),
    )
    conn.commit()


def _event(web_app, user, ts: float) -> None:
    repos.add_event(_db(web_app, user), Event(source="autotest", result=1.0, ts=ts))


def _snapshot(web_app, **kwargs):
    return admin_data.snapshot(web_app.state, COURSES[0], **kwargs)


async def test_snapshot_row_per_enrolled_student(web_app) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    bob, bob_user = await _learner(web_app, "Bob")
    cat, cat_user = await _learner(web_app, "Cat", enroll=False)
    _close_lesson(web_app, ann_user, "pandas_structures")

    snapshot = _snapshot(web_app)

    assert set(snapshot.rows) == {ann_user.id, bob_user.id}
    ann_row, bob_row = snapshot.rows[ann_user.id], snapshot.rows[bob_user.id]
    assert (ann_row.status, ann_row.closed) == ("in_progress", 1)
    assert ann_row.total > 1
    assert (bob_row.status, bob_row.closed) == ("survey", 0)
    assert cat_user.id not in snapshot.rows
    for client in (ann, bob, cat):
        await client.aclose()


async def test_snapshot_progress_matches_the_course_card(web_app) -> None:
    from llm_tutor.core.overview import course_progress

    client, user = await _learner(web_app, "Ann", survey_done=True)
    _close_lesson(web_app, user, "pandas_structures")

    row = _snapshot(web_app).rows[user.id]

    progress = course_progress(_db(web_app, user), settings=web_app.state.settings)
    assert (row.closed, row.total) == (progress.closed, progress.total)
    await client.aclose()


async def test_snapshot_marks_weak_lessons(web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    conn = _db(web_app, user)
    session_id = repos.ensure_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    assert state.route is not None
    # Текущее занятие слабым не считается (оно «текущее»), берём другое.
    other = next(step.concept_id for step in state.route.steps if step.concept_id != state.current_node_id)
    steps = [
        step.model_copy(update={"status": "closed", "weak": True}) if step.concept_id == other else step
        for step in state.route.steps
    ]
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"route": state.route.model_copy(update={"steps": steps})})
    )

    row = _snapshot(web_app).rows[user.id]

    assert row.weak == 1
    assert row.closed >= 1
    await client.aclose()


async def test_snapshot_last_activity_is_latest_event_or_message(web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    conn = _db(web_app, user)
    _event(web_app, user, 5000.0)
    repos.add_message(conn, repos.ensure_open_session(conn), "user", "привет", ts=7000.0)

    assert _snapshot(web_app).rows[user.id].last_activity == 7000.0

    _event(web_app, user, 9000.0)
    assert _snapshot(web_app, refresh=True).rows[user.id].last_activity == 9000.0
    await client.aclose()


async def test_snapshot_student_without_activity(web_app) -> None:
    client, user = await _learner(web_app, "Ann")

    assert _snapshot(web_app).rows[user.id].last_activity is None
    await client.aclose()


async def test_snapshot_student_without_db_file(web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    path = web_app.state.userdbs.path(user.id, COURSE)
    web_app.state.userdbs.close_all()
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)

    row = _snapshot(web_app).rows[user.id]

    assert row.status == "no_db"
    assert row.closed is None
    assert not path.exists()  # снимок не создаёт БД
    await client.aclose()


async def test_snapshot_broken_db_does_not_break_the_rest(web_app, monkeypatch, caplog) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    bob, bob_user = await _learner(web_app, "Bob", survey_done=True)
    pool = web_app.state.userdbs
    real_open = pool.open

    def open_(user_id, course_id):
        if user_id == bob_user.id:
            raise _sqlite3.DatabaseError("file is not a database")
        return real_open(user_id, course_id)

    monkeypatch.setattr(pool, "open", open_)

    with caplog.at_level(logging.ERROR):
        snapshot = _snapshot(web_app, refresh=True)

    assert snapshot.rows[bob_user.id].status == "error"
    assert snapshot.rows[ann_user.id].status == "in_progress"
    assert any("не открывается" in record.message for record in caplog.records)
    await ann.aclose()
    await bob.aclose()


async def test_snapshot_skips_student_whose_turn_is_running(web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    first = _snapshot(web_app)
    lock = web_app.state.userdbs.lock(user.id, COURSE)

    await lock.acquire()
    try:
        busy = _snapshot(web_app, refresh=True)
    finally:
        lock.release()

    row = busy.rows[user.id]
    assert row.busy
    # Данные берутся из прошлого снимка, а не из соединения посреди хода.
    assert (row.closed, row.total) == (first.rows[user.id].closed, first.rows[user.id].total)
    await client.aclose()


async def test_snapshot_busy_student_without_earlier_data(web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    lock = web_app.state.userdbs.lock(user.id, COURSE)

    await lock.acquire()
    try:
        row = _snapshot(web_app).rows[user.id]
    finally:
        lock.release()

    assert row.busy
    assert row.closed is None
    await client.aclose()


async def test_snapshot_is_cached_for_a_minute(web_app, monkeypatch) -> None:
    client, _ = await _learner(web_app, "Ann", survey_done=True)
    calls = []
    real_open = web_app.state.userdbs.open
    monkeypatch.setattr(web_app.state.userdbs, "open", lambda *a: calls.append(a) or real_open(*a))
    now = [1000.0]

    first = _snapshot(web_app, refresh=True, clock=lambda: now[0])
    opened = len(calls)
    assert opened > 0
    now[0] += 59
    assert _snapshot(web_app, clock=lambda: now[0]) is first
    assert len(calls) == opened

    assert _snapshot(web_app, refresh=True, clock=lambda: now[0]) is not first
    assert len(calls) > opened

    again = len(calls)
    now[0] += 61
    assert _snapshot(web_app, clock=lambda: now[0]).built_at == now[0]
    assert len(calls) > again
    await client.aclose()


async def test_snapshot_uses_materialized_summary_without_opening_dbs(web_app, monkeypatch) -> None:
    """После прогрева снимок читает course_progress, а не БД всех учеников."""
    client, user = await _learner(web_app, "Ann", survey_done=True)
    _close_lesson(web_app, user, "pandas_structures")
    _snapshot(web_app, refresh=True)  # прогрев: строка сводки записана
    web_app.state.admin_cache.clear()

    calls = []
    real_open = web_app.state.userdbs.open
    monkeypatch.setattr(web_app.state.userdbs, "open", lambda *a: calls.append(a) or real_open(*a))
    snap = _snapshot(web_app, clock=lambda: time.time() + 120)

    assert snap.rows[user.id].closed >= 1
    assert calls == [], "снимок открыл БД ученика вместо материализованной сводки"
    await client.aclose()


async def test_snapshot_cache_is_per_app(web_app, web_settings, web_llm) -> None:
    from llm_tutor.web.app import create_app

    client, _ = await _learner(web_app, "Ann", survey_done=True)
    other = create_app(web_settings, client=web_llm)
    try:
        assert web_app.state.admin_cache is not other.state.admin_cache
        _snapshot(web_app)
        assert web_app.state.admin_cache and not other.state.admin_cache
    finally:
        other.state.accounts.close()
    await client.aclose()


def test_account_search_escapes_like_wildcards(web_app) -> None:
    db = web_app.state.accounts
    for email, name in (
        ("a_b@example.com", "Под_черк"),
        ("axb@example.com", "Иксовый"),
        ("p@example.com", "100% учёбы"),
    ):
        accounts.create_user(db, email=email, name=name, password="секретный-пароль")

    assert [u.name for u in accounts.list_users(db, "_")] == ["Под_черк"]
    assert [u.name for u in accounts.list_users(db, "%")] == ["100% учёбы"]
    assert {u.name for u in accounts.list_users(db, "")} == {"Под_черк", "Иксовый", "100% учёбы"}
    assert accounts.list_users(db, "ПОД")[0].name == "Под_черк"  # без учёта регистра
    assert [u.email for u in accounts.list_users(db, "AXB@")] == ["axb@example.com"]


def test_active_sessions_count_only_live_ones(web_app) -> None:
    db = web_app.state.accounts
    user = accounts.create_user(db, email="a@example.com", name="А", password="секретный-пароль")
    accounts.create_session(db, user.id, now=100.0)
    accounts.create_session(db, user.id, now=100.0)

    assert accounts.active_sessions(db, user.id, 200.0) == 2
    assert accounts.active_sessions(db, user.id, 100.0 + accounts.SESSION_TTL + 1) == 0


async def _students_page(admin_client, **params) -> str:
    response = await admin_client.get("/admin/students", params=params)
    assert response.status_code == 200
    return response.text


async def test_students_page_lists_progress(admin, web_app) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    _close_lesson(web_app, ann_user, "pandas_structures")
    bob, _ = await _learner(web_app, "Bob")

    html = await _students_page(admin)

    for text in ("Ann", "ann@example.com", "Bob", "bob@example.com", ADMIN["name"]):
        assert text in html
    assert "В процессе" in html and "Анкета" in html
    assert "1 из" in html
    await ann.aclose()
    await bob.aclose()


async def test_students_page_search(admin, web_app) -> None:
    clients = [(await _learner(web_app, name, enroll=False))[0] for name in ("Ann", "Bob", "Anna")]

    html = await _students_page(admin, q="ANN")

    assert "ann@example.com" in html and "anna@example.com" in html
    assert "bob@example.com" not in html
    assert "Ничего не найдено" in await _students_page(admin, q="нет-такого")
    # Символы LIKE ищутся буквально.
    assert "Ничего не найдено" in await _students_page(admin, q="%")
    assert "Ничего не найдено" in await _students_page(admin, q="_")
    for client in clients:
        await client.aclose()


async def test_students_page_sorted_by_last_activity(admin, web_app) -> None:
    clients = []
    for name, ts in (("Aaa", 1000.0), ("Bbb", 3000.0), ("Ccc", None), ("Ddd", 2000.0)):
        client, user = await _learner(web_app, name)
        clients.append(client)
        if ts is not None:
            _event(web_app, user, ts)

    html = await _students_page(admin, refresh=1)

    order = [html.index(f"{name.lower()}@example.com") for name in ("Bbb", "Ddd", "Aaa", "Ccc")]
    assert order == sorted(order)  # новые первыми, без активности — в конце
    for client in clients:
        await client.aclose()


async def test_students_page_pagination(admin, web_app) -> None:
    db = web_app.state.accounts
    for index in range(PAGE_SIZE + 5):
        accounts.create_user(
            db, email=f"u{index:03d}@example.com", name=f"Уч{index:03d}", password="секретный-пароль"
        )

    first = await _students_page(admin)
    second = await _students_page(admin, page=2)

    # Без активности — по дате регистрации, новые первыми; админ зарегистрирован раньше всех.
    assert "u054@example.com" in first and "u054@example.com" not in second
    assert "u000@example.com" in second and "u000@example.com" not in first
    assert "root@example.com" in second
    assert await _students_page(admin, page=99) == second  # за границей — последняя
    assert await _students_page(admin, page="abc") == first  # мусор — первая
    assert await _students_page(admin, page=0) == first


async def test_summary_counts_active_and_finished(web_app) -> None:
    now = time.time()
    ann, ann_user = await _learner(web_app, "Ann")
    old, old_user = await _learner(web_app, "Old")
    _event(web_app, ann_user, now - 60)
    _event(web_app, old_user, now - 30 * 86400)

    summary = admin_data.summarize(_snapshot(web_app, refresh=True), total=2, now=now)

    assert (summary.total, summary.active_week, summary.finished) == (2, 1, 0)
    await ann.aclose()
    await old.aclose()


async def test_students_page_search_miss_has_a_message(admin) -> None:
    assert "Ничего не найдено" in await _students_page(admin, q="никто")


async def test_students_page_handles_broken_db(admin, web_app) -> None:
    ann, ann_user = await _learner(web_app, "Ann", survey_done=True)
    bob, bob_user = await _learner(web_app, "Bob", survey_done=True)
    web_app.state.userdbs.close_all()
    web_app.state.userdbs.path(bob_user.id, COURSE).write_bytes(b"not a database")

    html = await _students_page(admin, refresh=1)

    assert "Недоступен" in html
    assert "ann@example.com" in html and "bob@example.com" in html
    await ann.aclose()
    await bob.aclose()


async def test_students_page_escapes_names_and_hides_hashes(admin, web_app) -> None:
    accounts.create_user(
        web_app.state.accounts,
        email="x@example.com",
        name='<script>alert(1)</script>"><img src=x onerror=1>',
        password="секретный-пароль",
    )

    html = await _students_page(admin)

    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "scrypt$" not in html and "password_hash" not in html


async def test_students_page_refresh_button_and_time(admin) -> None:
    html = await _students_page(admin)

    assert "Данные на" in html
    assert "refresh=1" in html


async def test_students_page_search_keeps_query_in_pagination_links(admin, web_app) -> None:
    db = web_app.state.accounts
    for index in range(PAGE_SIZE + 2):
        accounts.create_user(
            db, email=f"zz{index:03d}@example.com", name=f"Зз{index:03d}", password="секретный-пароль"
        )

    html = await _students_page(admin, q="zz")

    assert "q=zz" in html and "page=2" in html


# --- Карточка ученика (Срез 65) ---

from llm_tutor.web import views


def _items(web_app, user, count: int):
    return [item for item in repos.get_items(_db(web_app, user)) if not item.final][:count]


def _answer(web_app, user, item, ts: float, *, result: float = 1.0, source: str = "autotest"):
    repos.add_event(
        _db(web_app, user),
        Event(
            source=source,
            result=result,
            item_id=item.id,
            concept_id=next(iter(item.concept_weights), None),
            ts=ts,
        ),
    )


async def _card(admin_client, user_id: int):
    return await admin_client.get(f"/admin/students/{user_id}")


async def test_card_header(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    accounts.create_session(web_app.state.accounts, user.id)

    response = await _card(admin, user.id)

    assert response.status_code == 200
    html = response.text
    for text in ("Ann", "ann@example.com", "Активных сессий"):
        assert text in html
    assert "админ</span>" not in html
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])
    assert "админ</span>" in (await _card(admin, root.id)).text
    await client.aclose()


async def test_card_shows_active_session_count(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")  # регистрация открыла одну сессию
    accounts.create_session(web_app.state.accounts, user.id)
    accounts.create_session(web_app.state.accounts, user.id, now=1.0)  # давно истекла

    html = (await _card(admin, user.id)).text

    assert 'data-sessions="2"' in html
    await client.aclose()


async def test_card_unknown_id_is_404(admin) -> None:
    assert (await _card(admin, 9999)).status_code == 404


async def test_card_non_numeric_id_does_not_crash(admin) -> None:
    response = await admin.get("/admin/students/abc")

    assert response.status_code in (404, 422)
    assert "Traceback" not in response.text


async def test_card_hidden_from_plain_student(student, web_app) -> None:
    user = accounts.find_user(web_app.state.accounts, ANN["email"])
    unknown = await student.get("/no-such-page")

    for path in (f"/admin/students/{user.id}", "/admin/students/9999", "/admin/students/abc"):
        response = await student.get(path)
        assert response.status_code == 404
        assert response.json() == unknown.json()


async def test_card_course_progress_topics_and_survey(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    _close_lesson(web_app, user, "pandas_structures")
    learning = views.learning_view(_db(web_app, user), settings=web_app.state.settings)

    html = (await _card(admin, user.id)).text

    assert "Модуль 1" in html
    first = next(topic for topic in learning["topics"] if topic["id"] == "pandas_structures")
    assert first["name"] in html
    assert f"{first['mastery']}%" in html
    assert first["label"] in html  # статус — как в профиле ученика
    assert "уверенно" in html or "мало данных" in html
    # Результаты анкеты — то же, что видит сам ученик в профиле.
    assert learning["self_assessment"]
    assert learning["self_assessment"][0]["title"] in html
    assert f"Решено заданий: {learning['solved']}" in html
    await client.aclose()


async def test_card_marks_weak_lessons(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    conn = _db(web_app, user)
    session_id = repos.ensure_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    other = next(step.concept_id for step in state.route.steps if step.concept_id != state.current_node_id)
    steps = [
        step.model_copy(update={"status": "closed", "weak": True}) if step.concept_id == other else step
        for step in state.route.steps
    ]
    repos.update_session_state(
        conn, session_id, state.model_copy(update={"route": state.route.model_copy(update={"steps": steps})})
    )

    html = (await _card(admin, user.id)).text

    assert views.WEAK_LABEL in html
    await client.aclose()


async def test_card_recent_answers_are_the_latest_fifty(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    items = _items(web_app, user, 3)
    for index in range(55):
        # Шаг в минуту: время в таблице выводится с точностью до минуты.
        _answer(web_app, user, items[index % 3], 100_000.0 + 60 * index, result=1.0 if index % 2 else 0.0)

    html = (await _card(admin, user.id)).text

    assert html.count('class="answer-row"') == 50
    # Новые первыми: последний ответ (54-й) выше первого из показанных (5-го).
    newest, oldest_shown, dropped = (admin_data.format_time(100_000.0 + 60 * n) for n in (54, 5, 4))
    assert html.index(newest) < html.index(oldest_shown)
    assert dropped not in html
    await client.aclose()


async def test_card_answer_row_details(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    item, = _items(web_app, user, 1)
    conn = _db(web_app, user)
    conn.execute("UPDATE items SET prompt = ? WHERE id = ?", ("а" * 200, item.id))
    conn.commit()
    _answer(web_app, user, item, 2000.0, result=1.0, source="autotest")
    _answer(web_app, user, item, 2001.0, result=0.0, source="checked")
    _answer(web_app, user, item, 2002.0, result=0.4, source="rubric")

    html = (await _card(admin, user.id)).text

    assert "а" * 80 in html and "а" * 81 not in html  # условие обрезано до 80 символов
    assert f"#{item.id}" in html
    assert "верно" in html and "неверно" in html and "40%" in html
    for source in ("autotest", "checked", "rubric"):
        assert source in html
    await client.aclose()


async def test_card_answer_for_missing_item_does_not_crash(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    item, = _items(web_app, user, 1)
    conn = _db(web_app, user)
    _answer(web_app, user, item, 3000.0)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM items WHERE id = ?", (item.id,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    response = await _card(admin, user.id)

    assert response.status_code == 200
    assert "answer-row" in response.text
    await client.aclose()


async def test_card_never_shows_chat_messages(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    conn = _db(web_app, user)
    session_id = repos.ensure_open_session(conn)
    repos.add_message(conn, session_id, "user", "СЕКРЕТНАЯ-РЕПЛИКА-УЧЕНИКА", ts=1.0)
    repos.add_message(conn, session_id, "assistant", "СЕКРЕТНЫЙ-ОТВЕТ-ТЬЮТОРА", ts=2.0)

    html = (await _card(admin, user.id)).text

    assert "СЕКРЕТНАЯ-РЕПЛИКА-УЧЕНИКА" not in html
    assert "СЕКРЕТНЫЙ-ОТВЕТ-ТЬЮТОРА" not in html
    await client.aclose()


async def test_card_busy_student_shows_banner_and_no_course_data(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", survey_done=True)
    lock = web_app.state.userdbs.lock(user.id, COURSE)

    await lock.acquire()
    try:
        response = await _card(admin, user.id)
    finally:
        lock.release()

    assert response.status_code == 200
    assert "сейчас отвечает" in response.text
    assert "Модуль 1" not in response.text
    assert "ann@example.com" in response.text  # шапка — из аккаунтов, она не зависит от хода
    await client.aclose()


async def test_card_student_not_enrolled(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)

    html = (await _card(admin, user.id)).text

    assert "не записан" in html.lower()
    await client.aclose()


async def test_card_enrolled_without_db_file(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    path = web_app.state.userdbs.path(user.id, COURSE)
    web_app.state.userdbs.close_all()
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)

    response = await _card(admin, user.id)

    assert response.status_code == 200
    assert "нет данных" in response.text.lower()
    assert not path.exists()
    await client.aclose()


async def test_card_broken_db(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    web_app.state.userdbs.close_all()
    web_app.state.userdbs.path(user.id, COURSE).write_bytes(b"not a database")

    response = await _card(admin, user.id)

    assert response.status_code == 200
    assert "недоступны" in response.text.lower()
    await client.aclose()


async def test_card_admin_log_empty_and_filled(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    assert "Действий не было" in (await _card(admin, user.id)).text

    db = web_app.state.accounts
    db.execute(
        "INSERT INTO admin_log (ts, admin_id, action, target_id, details) VALUES (?, ?, ?, ?, ?)",
        (4000.0, root.id, "reset_password", user.id, None),
    )
    db.execute(
        "INSERT INTO admin_log (ts, admin_id, action, target_id, details) VALUES (?, ?, ?, ?, ?)",
        (4001.0, 9999, "close_sessions", user.id, None),  # админ уже удалён
    )
    db.execute(
        "INSERT INTO admin_log (ts, admin_id, action, target_id, details) VALUES (?, ?, ?, ?, ?)",
        (4002.0, root.id, "reset_password", root.id, None),  # над другим аккаунтом
    )
    db.commit()

    html = (await _card(admin, user.id)).text

    assert "Действий не было" not in html
    assert "Сброс пароля" in html and "Закрытие сессий" in html
    assert ADMIN["name"] in html  # имя админа из журнала
    assert html.count('class="log-row"') == 2
    await client.aclose()


async def test_card_admin_log_is_limited_to_twenty(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])
    for index in range(25):
        web_app.state.accounts.execute(
            "INSERT INTO admin_log (ts, admin_id, action, target_id) VALUES (?, ?, 'close_sessions', ?)",
            (5000.0 + index, root.id, user.id),
        )
    web_app.state.accounts.commit()

    html = (await _card(admin, user.id)).text

    assert html.count('class="log-row"') == 20
    await client.aclose()


async def test_card_escapes_text_and_hides_hashes(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    web_app.state.accounts.execute(
        "UPDATE users SET name = ? WHERE id = ?", ('<img src=x onerror=alert(1)>', user.id)
    )
    web_app.state.accounts.commit()
    item, = _items(web_app, user, 1)
    conn = _db(web_app, user)
    conn.execute("UPDATE items SET prompt = ? WHERE id = ?", ("<script>alert(2)</script> условие", item.id))
    conn.commit()
    _answer(web_app, user, item, 6000.0)

    html = (await _card(admin, user.id)).text

    assert "<img src=x" not in html and "<script>alert(2)" not in html
    assert "&lt;img" in html and "&lt;script&gt;" in html
    assert "scrypt$" not in html and "password_hash" not in html
    await client.aclose()


async def test_students_list_links_to_cards(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")

    # Курс едет в ссылке: карточка показывается по выбранному в шапке курсу.
    assert f'href="/admin/students/{user.id}?course=mlcourse"' in await _students_page(admin)
    await client.aclose()


# --- Действия над аккаунтом (Срез 66) ---

import json


def _reset_url(user_id: int) -> str:
    return f"/api/admin/students/{user_id}/reset-password"


def _close_url(user_id: int) -> str:
    return f"/api/admin/students/{user_id}/close-sessions"


async def _login(web_app, email: str, password: str) -> httpx.Response:
    async with _client(web_app) as fresh:
        return await fresh.post("/api/auth/login", json={"email": email, "password": password})


def _log_rows(web_app) -> list[tuple]:
    return web_app.state.accounts.execute(
        "SELECT admin_id, action, target_id, details FROM admin_log ORDER BY id"
    ).fetchall()


async def test_reset_password_gives_a_one_time_password(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)

    response = await admin.post(_reset_url(user.id), json={})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    password = response.json()["password"]
    assert len(password) >= 16
    assert (await _login(web_app, "ann@example.com", password)).status_code == 200
    assert (await _login(web_app, "ann@example.com", "секретный-пароль")).status_code == 401
    await client.aclose()


async def test_reset_password_closes_all_student_sessions_but_not_the_admins(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    other = _client(web_app)
    await other.post("/api/auth/login", json={"email": "ann@example.com", "password": "секретный-пароль"})
    assert (await client.get("/api/me")).status_code == 200
    assert (await other.get("/api/me")).status_code == 200

    await admin.post(_reset_url(user.id), json={})

    assert (await client.get("/api/me")).status_code == 401
    assert (await other.get("/api/me")).status_code == 401
    assert (await admin.get("/api/me")).status_code == 200  # сессия админа цела
    await client.aclose()
    await other.aclose()


async def test_reset_password_lifts_the_login_limiter(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    for _ in range(6):
        await _login(web_app, "ann@example.com", "не-тот-пароль")
    assert (await _login(web_app, "ann@example.com", "секретный-пароль")).status_code == 429

    password = (await admin.post(_reset_url(user.id), json={})).json()["password"]

    assert (await _login(web_app, "ann@example.com", password)).status_code == 200
    await client.aclose()


async def test_two_resets_give_different_passwords(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)

    first = (await admin.post(_reset_url(user.id), json={})).json()["password"]
    second = (await admin.post(_reset_url(user.id), json={})).json()["password"]

    assert first != second
    assert (await _login(web_app, "ann@example.com", first)).status_code == 401
    await client.aclose()


async def test_close_sessions_reports_live_sessions_and_keeps_the_password(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)  # одна сессия от регистрации
    accounts.create_session(web_app.state.accounts, user.id)
    accounts.create_session(web_app.state.accounts, user.id)

    response = await admin.post(_close_url(user.id), json={})

    assert response.status_code == 200
    assert response.json() == {"closed": 3}
    assert accounts.active_sessions(web_app.state.accounts, user.id) == 0
    assert (await client.get("/api/me")).status_code == 401
    assert (await _login(web_app, "ann@example.com", "секретный-пароль")).status_code == 200
    assert (await admin.get("/api/me")).status_code == 200
    await client.aclose()


async def test_close_sessions_without_any_returns_zero(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    await _call(admin, "POST", _close_url(user.id))

    response = await admin.post(_close_url(user.id), json={})

    assert response.json() == {"closed": 0}
    await client.aclose()


@pytest.mark.parametrize("url", [_reset_url, _close_url])
async def test_admin_cannot_act_on_themselves(admin, web_app, url) -> None:
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    response = await admin.post(url(root.id), json={})

    assert response.status_code == 400
    assert "профил" in response.json()["detail"]["message"]
    assert (await admin.get("/api/me")).status_code == 200  # сессия цела
    assert (await _login(web_app, ADMIN["email"], ADMIN["password"])).status_code == 200  # пароль прежний
    assert _log_rows(web_app) == []


@pytest.mark.parametrize("url", [_reset_url, _close_url])
async def test_unknown_student_is_404(admin, url) -> None:
    response = await admin.post(url(9999), json={})

    assert response.status_code == 404
    assert response.json()["detail"]["message"]


@pytest.mark.parametrize("url", [_reset_url, _close_url])
async def test_plain_student_cannot_act_and_nothing_changes(student, web_app, url) -> None:
    victim, victim_user = await _learner(web_app, "Bob", enroll=False)
    unknown = await student.post("/api/no-such-endpoint", json={})

    response = await student.post(url(victim_user.id), json={})

    assert response.status_code == 404
    assert response.json() == unknown.json()
    assert (await victim.get("/api/me")).status_code == 200
    assert (await _login(web_app, "bob@example.com", "секретный-пароль")).status_code == 200
    assert _log_rows(web_app) == []
    await victim.aclose()


@pytest.mark.parametrize("url", [_reset_url, _close_url])
async def test_guest_gets_401(web, url) -> None:
    assert (await web.post(url(1), json={})).status_code == 401


@pytest.mark.parametrize("url", [_reset_url, _close_url])
async def test_foreign_origin_and_non_json_are_rejected(admin, web_app, url) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)

    foreign = await admin.post(url(user.id), json={}, headers={"Origin": "http://evil.example"})
    cross_site = await admin.post(url(user.id), json={}, headers={"Sec-Fetch-Site": "cross-site"})
    form = await admin.post(url(user.id), data={"x": "1"})

    assert foreign.status_code == 403
    assert cross_site.status_code == 403
    assert form.status_code == 415
    assert (await client.get("/api/me")).status_code == 200  # ничего не сделано
    assert _log_rows(web_app) == []
    await client.aclose()


async def test_reset_password_rejects_get(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)

    assert (await admin.get(_reset_url(user.id))).status_code == 405
    await client.aclose()


async def test_actions_are_logged_without_secrets(admin, web_app, caplog) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    with caplog.at_level(logging.DEBUG):
        password = (await admin.post(_reset_url(user.id), json={})).json()["password"]
        await admin.post(_close_url(user.id), json={})

    rows = _log_rows(web_app)
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (root.id, "reset_password", user.id),
        (root.id, "close_sessions", user.id),
    ]
    dump = json.dumps([list(row) for row in rows], ensure_ascii=False)
    assert password not in dump
    assert password not in caplog.text
    # Пароль нигде в БД аккаунтов, кроме хеша, не лежит.
    for line in web_app.state.accounts.iterdump():
        if "INSERT INTO" in line and "password_hash" not in line:
            assert password not in line
    await client.aclose()


async def test_card_shows_actions_after_they_happen(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    assert "Действий не было" in (await _card(admin, user.id)).text

    await admin.post(_reset_url(user.id), json={})
    await admin.post(_close_url(user.id), json={})

    html = (await _card(admin, user.id)).text
    assert "Сброс пароля" in html and "Закрытие сессий" in html
    assert 'data-sessions="0"' in html
    await client.aclose()


async def test_failed_action_is_not_logged(admin, web_app) -> None:
    await admin.post(_reset_url(9999), json={})
    await admin.post(_close_url(9999), json={})

    assert _log_rows(web_app) == []


async def test_card_has_action_buttons_for_others_but_not_for_self(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    other = (await _card(admin, user.id)).text
    own = (await _card(admin, root.id)).text

    assert 'data-action="reset-password"' in other and 'data-action="close-sessions"' in other
    assert 'data-action="reset-password"' not in own
    assert "профил" in own  # подсказка, где менять свой пароль
    await client.aclose()


# --- Аналитика курса (Срез 67) ---

import re
from dataclasses import replace

from llm_tutor.web import courses as web_courses


def _data():
    return web_courses.course_data(COURSES[0])


def _lesson_ids() -> list[str]:
    return [node.id for node in _data().nodes]


def _set_state(web_app, user, *, current: str | None, closed=(), weak=()) -> None:
    """Состояние занятий ученика после анкеты: текущее, закрытые и слабые."""
    conn = _db(web_app, user)
    session_id = repos.ensure_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    assert state.route is not None
    steps = [
        step.model_copy(
            update={
                "status": "closed" if step.concept_id in closed else step.status,
                "weak": step.concept_id in weak,
            }
        )
        for step in state.route.steps
    ]
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(update={"current_node_id": current, "route": state.route.model_copy(update={"steps": steps})}),
    )


async def _funnel_students(web_app):
    """Четыре ученика: у каждого своя картина прохождения первых занятий."""
    l0, l1, l2, _ = _lesson_ids()[:4]
    made = []
    for name, state in (
        ("Sa", dict(current=l2, closed=(l0, l1))),
        ("Sb", dict(current=l1, closed=(l0,), weak=(l0,))),
        ("Sc", dict(current=l0)),
    ):
        client, user = await _learner(web_app, name, survey_done=True)
        _set_state(web_app, user, **state)
        made.append(client)
    client, _ = await _learner(web_app, "Sd")  # без анкеты
    made.append(client)
    return made


def _lesson(funnel, lesson_id: str):
    for module in funnel:
        for lesson in module.lessons:
            if lesson.id == lesson_id:
                return lesson
    raise AssertionError(lesson_id)


async def test_funnel_counts_reached_closed_and_weak(web_app) -> None:
    clients = await _funnel_students(web_app)
    l0, l1, l2, l3 = _lesson_ids()[:4]

    funnel = admin_data.funnel(_snapshot(web_app), _data())

    first, second, third, untouched = (_lesson(funnel, lesson) for lesson in (l0, l1, l2, l3))
    assert (first.reached, first.closed, first.weak) == (3, 2, 1)
    assert first.rate == pytest.approx(2 / 3)
    assert (second.reached, second.closed, second.weak) == (2, 1, 0)
    assert (third.reached, third.closed, third.weak) == (1, 0, 0)
    assert third.rate == 0.0
    # До занятия никто не дошёл — нули, а не пропуск строки; доля не считается.
    assert (untouched.reached, untouched.closed, untouched.weak, untouched.rate) == (0, 0, 0, None)
    for client in clients:
        await client.aclose()


async def test_funnel_student_without_survey_never_reached_anything(web_app) -> None:
    clients = await _funnel_students(web_app)

    funnel = admin_data.funnel(_snapshot(web_app), _data())

    assert max(lesson.reached for module in funnel for lesson in module.lessons) <= 3  # Sd не в счёте
    for client in clients:
        await client.aclose()


async def test_funnel_invariants_and_agreement_with_the_list(web_app) -> None:
    clients = await _funnel_students(web_app)
    snap = _snapshot(web_app)

    funnel = admin_data.funnel(snap, _data())

    lessons = [lesson for module in funnel for lesson in module.lessons]
    students = len(snap.rows)
    for lesson in lessons:
        assert 0 <= lesson.weak <= lesson.closed <= lesson.reached <= students
    # Сумма закрытых по занятиям сходится с прогрессом учеников в списке.
    assert sum(lesson.closed for lesson in lessons) == sum(row.closed for row in snap.rows.values())
    for client in clients:
        await client.aclose()


async def test_funnel_follows_course_order_and_skips_unpublished_modules(web_app) -> None:
    data = _data()
    topics = tuple(topic.model_copy(update={"published": topic.number != 2}) for topic in data.topics)

    funnel = admin_data.funnel(_snapshot(web_app), replace(data, topics=topics))

    assert [module.number for module in funnel] == [topic.number for topic in data.topics if topic.number != 2]
    published = {node.id for node in data.nodes if node.topic_id != 2}
    assert [lesson.id for module in funnel for lesson in module.lessons] == [
        node.id for node in data.nodes if node.id in published
    ]
    assert all(module.title for module in funnel)


async def test_funnel_empty_course(web_app) -> None:
    funnel = admin_data.funnel(_snapshot(web_app), _data())

    lessons = [lesson for module in funnel for lesson in module.lessons]
    assert lessons and all((lesson.reached, lesson.closed, lesson.weak) == (0, 0, 0) for lesson in lessons)
    assert all(lesson.rate is None for lesson in lessons)


async def test_funnel_ignores_students_without_data(web_app) -> None:
    clients = await _funnel_students(web_app)
    client, user = await _learner(web_app, "Broken", survey_done=True)
    web_app.state.userdbs.close_all()
    web_app.state.userdbs.path(user.id, COURSE).write_bytes(b"not a database")

    funnel = admin_data.funnel(_snapshot(web_app, refresh=True), _data())

    assert _lesson(funnel, _lesson_ids()[0]).reached == 3
    for c in (*clients, client):
        await c.aclose()


def _pick_items(count: int):
    return [item for item in _data().items if not item.final][:count]


async def _course_page(admin_client, **params) -> str:
    response = await admin_client.get("/admin/course", params=params)
    assert response.status_code == 200
    return response.text


async def test_course_page_shows_funnel_rows(admin, web_app) -> None:
    clients = await _funnel_students(web_app)
    l0 = _lesson_ids()[0]

    html = await _course_page(admin)

    row = re.search(rf'<tr class="lesson-row"[^>]*data-lesson="{l0}"([^>]*)>', html)
    assert row, "нет строки занятия"
    attrs = row.group(1)
    assert 'data-reached="3"' in attrs and 'data-closed="2"' in attrs and 'data-weak="1"' in attrs
    assert "67%" in html  # доля закрытия первого занятия
    assert "Модуль 1" in html
    for client in clients:
        await client.aclose()


async def test_course_page_without_students(admin) -> None:
    html = await _course_page(admin)

    assert "Модуль 1" in html  # воронка строится и без учеников — нулями
    assert 'data-reached="0"' in html


async def test_course_page_time_and_refresh(admin) -> None:
    html = await _course_page(admin)

    assert "Данные на" in html
    assert "refresh=1" in html


async def test_course_page_and_students_page_share_one_snapshot(admin, web_app, monkeypatch) -> None:
    client, _ = await _learner(web_app, "Ann", survey_done=True)
    calls = []
    real_open = web_app.state.userdbs.open
    monkeypatch.setattr(web_app.state.userdbs, "open", lambda *a: calls.append(a) or real_open(*a))

    await _students_page(admin, refresh=1)
    opened = len(calls)
    assert opened > 0
    await _course_page(admin)
    assert len(calls) == opened  # снимок общий и свежий

    await _course_page(admin, refresh=1)
    assert len(calls) > opened
    await client.aclose()


async def test_course_page_requires_admin(student) -> None:
    assert (await student.get("/admin/course")).status_code == 404


async def test_admin_tabs_link_to_the_course_page(admin) -> None:
    html = await _students_page(admin)

    # Курс едет и во вкладке: иначе переход по ней переключил бы админку
    # на первый опубликованный курс.
    assert 'href="/admin/course?course=mlcourse"' in html


# --- Аудит среза 68: находки общего ревью ---

import math

from llm_tutor.course.graph import CourseGraph
from llm_tutor.student import diagnostic


def _two_concept_item():
    return next(item for item in _data().items if len(item.concept_weights) >= 2 and not item.final)


def _core_answer(web_app, user, item, ts: float, score: float = 1.0) -> int:
    """Ответ так, как его пишет ядро: по событию на каждый концепт задания."""
    conn = _db(web_app, user)
    main = max(item.concept_weights, key=item.concept_weights.__getitem__)
    events, _ = diagnostic.plan_evidence(
        conn, CourseGraph.load(conn), item, main, score, source="checked", now=ts, settings=web_app.state.settings
    )
    for event in events:
        repos.add_event(conn, event)
    return len(events)


async def test_one_answer_to_a_multi_concept_item_is_one_answer(admin, web_app) -> None:
    """M1: ядро пишет событие на каждый концепт, а ответ ученика — один."""
    client, user = await _learner(web_app, "Ann")
    item = _two_concept_item()
    assert _core_answer(web_app, user, item, 5000.0) >= 2  # в журнале несколько событий

    html = (await _card(admin, user.id)).text

    assert html.count('class="answer-row"') == 1
    await client.aclose()


async def test_two_separate_answers_stay_two(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    item = _two_concept_item()
    _core_answer(web_app, user, item, 5000.0, 0.0)
    _core_answer(web_app, user, item, 5001.0, 1.0)

    assert (await _card(admin, user.id)).text.count('class="answer-row"') == 2
    await client.aclose()


async def test_card_names_the_lesson_by_the_main_concept(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    item = _two_concept_item()
    _core_answer(web_app, user, item, 5000.0)
    main = max(item.concept_weights, key=item.concept_weights.__getitem__)
    lesson = next(node.name for node in _data().nodes if node.id == main)

    assert lesson in (await _card(admin, user.id)).text
    await client.aclose()


async def test_no_hint_or_time_columns_without_a_data_source(admin, web_app) -> None:
    """M2: ``hints_used``/``time_spent`` нигде не пишутся — столбцов нет."""
    client, user = await _learner(web_app, "Ann")
    _answer(web_app, user, _pick_items(1)[0], 100.0)

    for html in (await _card(admin, user.id)).text, await _course_page(admin):
        assert "Подсказок" not in html and "Время решения" not in html
    await client.aclose()


@pytest.mark.parametrize("raw", ["99999999999999999999999", str(2**63), "-5", "0"])
async def test_out_of_range_ids_are_not_found(admin, raw: str) -> None:
    """M3: id вне диапазона SQLite — 404, а не 500."""
    assert (await admin.get(f"/admin/students/{raw}")).status_code == 404
    for action in ("reset-password", "close-sessions"):
        response = await admin.post(f"/api/admin/students/{raw}/{action}", json={})
        assert response.status_code == 404, (raw, action)


@pytest.mark.parametrize("state", ["{garbage", '{"current_node_id": 123}', "null", "[]"])
async def test_unreadable_session_state_does_not_break_the_pages(admin, web_app, state: str) -> None:
    """M4: битое состояние сессии одного ученика не роняет списки и аналитику."""
    good, _ = await _learner(web_app, "Ann", survey_done=True)
    bad, bad_user = await _learner(web_app, "Bob", survey_done=True)
    conn = _db(web_app, bad_user)
    conn.execute("UPDATE sessions SET state = ?", (state,))
    conn.commit()

    listing = await admin.get("/admin/students", params={"refresh": 1})
    course = await admin.get("/admin/course")
    card = await _card(admin, bad_user.id)

    assert listing.status_code == course.status_code == card.status_code == 200
    assert "Недоступен" in listing.text and "ann@example.com" in listing.text
    assert "недоступны" in card.text.lower()
    await good.aclose()
    await bad.aclose()


@pytest.mark.parametrize("ts", [1.7e12, -5.0, 1e20, math.inf])  # NaN в SQLite — NULL, а ts NOT NULL
async def test_garbage_timestamps_do_not_break_the_pages(admin, web_app, ts: float) -> None:
    """L2: мусорная метка времени в журнале — прочерк, а не 500."""
    client, user = await _learner(web_app, "Ann")
    item, = _items(web_app, user, 1)
    conn = _db(web_app, user)
    conn.execute(
        "INSERT INTO events (ts, concept_id, item_id, source, result) VALUES (?, NULL, ?, 'autotest', 1.0)",
        (ts, item.id),
    )
    conn.commit()

    assert (await admin.get("/admin/students", params={"refresh": 1})).status_code == 200
    assert (await _card(admin, user.id)).status_code == 200
    assert (await _course_page(admin))
    await client.aclose()


def test_format_time_survives_garbage() -> None:
    for ts in (1e20, math.inf, -math.inf, math.nan, 1.7e12):
        assert admin_data.format_time(ts) == "—" or "." in admin_data.format_time(ts)
    assert admin_data.format_time(None) == "—"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/admin/students"),
        ("PUT", "/admin/course"),
        ("DELETE", "/admin/students/1"),
        ("GET", "/admin/"),
        ("GET", "/admin/students/"),
        ("GET", "/admin/course/"),
        ("GET", "/api/admin/students/1/reset-password"),
        ("GET", "/api/admin/students/1/close-sessions"),
        ("POST", "/api/admin/students/abc/reset-password"),
        ("GET", "/api/admin"),
        ("GET", "/admin"),
    ],
)
async def test_plain_student_cannot_tell_admin_paths_from_unknown_ones(student, method: str, path: str) -> None:
    """L1: ни 405, ни 307 со слэшем: не-админу всё под /admin — как несуществующее."""
    unknown = await student.request("GET", "/no/such/page/")

    response = await student.request(method, path, json=None if method == "GET" else {})

    assert (response.status_code, response.json()) == (unknown.status_code, unknown.json()) == (404, {"detail": "Not Found"})


async def test_cloak_does_not_catch_lookalike_paths(student) -> None:
    for path in ("/administrator", "/admins", "/api/administrators"):
        response = await student.get(path)
        assert response.status_code == 404  # обычный 404, а не плащ — просто нет такой страницы


async def test_admin_still_sees_real_405s(admin) -> None:
    assert (await admin.post("/admin/students", json={})).status_code == 405


async def test_admin_pages_are_not_cached_by_the_browser(admin) -> None:
    """Страницы с чужими email не должны вернуться по «Назад» после выхода."""
    for path in ("/admin/students", "/admin/course"):
        assert (await admin.get(path)).headers["cache-control"] == "no-store"


async def test_busy_student_is_not_read_mid_turn(web_app) -> None:
    """L6: пока ход идёт, снимок не читает соединение ученика — даже если данные поменялись."""
    client, user = await _learner(web_app, "Ann")
    _event(web_app, user, 5000.0)
    first = _snapshot(web_app)
    lock = web_app.state.userdbs.lock(user.id, COURSE)

    await lock.acquire()
    try:
        _event(web_app, user, 9_000_000_000.0)  # «ход» успел записать, но не закончил
        busy = _snapshot(web_app, refresh=True)
    finally:
        lock.release()

    assert busy.rows[user.id].busy
    assert busy.rows[user.id].last_activity == first.rows[user.id].last_activity == 5000.0
    assert _snapshot(web_app, refresh=True).rows[user.id].last_activity == 9_000_000_000.0
    await client.aclose()


# --- Срез 69: компактная страница курса и выдача роли из веба ---


async def test_course_page_modules_are_collapsed(admin, web_app) -> None:
    html = await _course_page(admin)
    published = [topic for topic in _data().topics if topic.published]

    blocks = re.findall(r"<details[^>]*>", html)
    assert len(blocks) == len(published)
    assert all('class="admin-module"' in block and " open" not in block for block in blocks)


async def test_course_page_has_no_hard_items(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    _answer(web_app, user, _pick_items(1)[0], 100.0)

    html = await _course_page(admin)

    assert "Трудные задания" not in html
    assert "data-item=" not in html
    assert "admin-filters" not in html
    await client.aclose()


async def test_module_summary_counts_started_and_finished(web_app) -> None:
    clients = await _funnel_students(web_app)
    data = _data()
    first_module = [node.id for node in data.nodes if node.topic_id == 1]
    finisher, finisher_user = await _learner(web_app, "Fin", survey_done=True)
    _set_state(web_app, finisher_user, current=None, closed=tuple(first_module))

    funnel = admin_data.funnel(_snapshot(web_app), data)

    module = next(block for block in funnel if block.number == 1)
    # Sa, Sb, Sc и Fin дошли хотя бы до одного занятия; Sd без анкеты — нет.
    assert module.started == 4
    assert module.finished == 1
    second = next(block for block in funnel if block.number == 2)
    assert (second.started, second.finished) == (0, 0)
    for client in (*clients, finisher):
        await client.aclose()


async def test_module_summary_is_shown_in_the_collapsed_row(admin, web_app) -> None:
    clients = await _funnel_students(web_app)

    html = await _course_page(admin)

    summary = re.search(r'<details class="admin-module" data-module="1">\s*<summary>(.*?)</summary>', html, re.S)
    assert summary and "начали 3" in summary.group(1) and "прошли 0" in summary.group(1)
    for client in clients:
        await client.aclose()


async def test_course_page_ignores_old_filter_params(admin) -> None:
    assert await _course_page(admin, module="abc", type="zzz")


def _grant_url(user_id: int) -> str:
    return f"/api/admin/students/{user_id}/grant-admin"


def _revoke_url(user_id: int) -> str:
    return f"/api/admin/students/{user_id}/revoke-admin"


async def test_admin_grants_the_role_in_the_web(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    assert (await client.get("/admin/students")).status_code == 404

    response = await admin.post(_grant_url(user.id), json={})

    assert response.status_code == 200
    assert response.json() == {"is_admin": True}
    assert accounts.get_user(web_app.state.accounts, user.id).is_admin
    # Роль действует сразу, без перелогина.
    assert (await client.get("/admin/students")).status_code == 200
    await client.aclose()


async def test_admin_revokes_the_role_and_access_ends_at_once(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    await admin.post(_grant_url(user.id), json={})
    assert (await client.get("/admin/students")).status_code == 200

    response = await admin.post(_revoke_url(user.id), json={})

    assert response.json() == {"is_admin": False}
    assert (await client.get("/admin/students")).status_code == 404
    assert (await client.get("/api/me")).status_code == 200  # аккаунт и сессия живы
    await client.aclose()


async def test_role_changes_are_logged_once(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    await admin.post(_grant_url(user.id), json={})
    await admin.post(_grant_url(user.id), json={})  # повтор — без новой записи
    await admin.post(_revoke_url(user.id), json={})

    assert [(row[0], row[1], row[2]) for row in _log_rows(web_app)] == [
        (root.id, "grant_admin", user.id),
        (root.id, "revoke_admin", user.id),
    ]
    html = (await _card(admin, user.id)).text
    assert "Выдана роль админа" in html and "Снята роль админа" in html
    await client.aclose()


@pytest.mark.parametrize("url", [_grant_url, _revoke_url])
async def test_admin_cannot_change_own_role(admin, web_app, url) -> None:
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    response = await admin.post(url(root.id), json={})

    assert response.status_code == 400
    assert accounts.get_user(web_app.state.accounts, root.id).is_admin  # роль на месте
    assert (await admin.get("/admin/students")).status_code == 200
    assert _log_rows(web_app) == []


@pytest.mark.parametrize("url", [_grant_url, _revoke_url])
async def test_plain_student_cannot_change_roles(student, web_app, url) -> None:
    ann = accounts.find_user(web_app.state.accounts, ANN["email"])
    other, other_user = await _learner(web_app, "Bob", enroll=False)

    for target in (ann.id, other_user.id):
        response = await student.post(url(target), json={})
        assert response.status_code == 404
    assert not accounts.get_user(web_app.state.accounts, ann.id).is_admin
    assert not accounts.get_user(web_app.state.accounts, other_user.id).is_admin
    assert _log_rows(web_app) == []
    await other.aclose()


@pytest.mark.parametrize("url", [_grant_url, _revoke_url])
async def test_role_endpoints_unknown_guest_and_csrf(admin, web, web_app, url) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)

    assert (await admin.post(url(9999), json={})).status_code == 404
    assert (await admin.post(url(user.id), json={}, headers={"Origin": "http://evil.example"})).status_code == 403
    assert (await admin.post(url(user.id), data={"x": "1"})).status_code == 415
    async with _client(web_app) as guest:
        assert (await guest.post(url(user.id), json={})).status_code == 401
    assert not accounts.get_user(web_app.state.accounts, user.id).is_admin
    await client.aclose()


async def test_admin_can_revoke_another_admin(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    accounts.set_admin(web_app.state.accounts, user.id, True)

    assert (await admin.post(_revoke_url(user.id), json={})).status_code == 200
    assert not accounts.get_user(web_app.state.accounts, user.id).is_admin
    await client.aclose()


async def test_card_shows_the_right_role_button(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann", enroll=False)
    root = accounts.find_user(web_app.state.accounts, ADMIN["email"])

    plain = (await _card(admin, user.id)).text
    await admin.post(_grant_url(user.id), json={})
    promoted = (await _card(admin, user.id)).text
    own = (await _card(admin, root.id)).text

    assert 'data-action="grant-admin"' in plain and 'data-action="revoke-admin"' not in plain
    assert 'data-action="revoke-admin"' in promoted and 'data-action="grant-admin"' not in promoted
    assert 'data-action="grant-admin"' not in own and 'data-action="revoke-admin"' not in own
    await client.aclose()


async def test_analytics_counts_items_hints_and_time(web_app) -> None:
    """Аналитика курса: попытки, подсказки, время и расписание повторений."""
    client, user = await _learner(web_app, "Ann")
    conn = _db(web_app, user)
    now = time.time()
    repos.add_event(
        conn,
        Event(source="checked", result=1.0, item_id=1, hints_used=2, time_spent=10.0, ts=now - 100),
    )
    repos.add_event(
        conn,
        Event(source="checked", result=0.0, item_id=1, hints_used=0, time_spent=30.0, ts=now - 50),
    )
    repos.upsert_review(conn, "pandas_structures", 1, now - 2 * 86400)
    repos.add_review_log(
        conn,
        "pandas_structures",
        due_at=now - 100,
        answered_at=now - 200,
        passed=True,
        step=1,
    )
    conn.commit()

    snap = admin_data.snapshot(web_app.state, COURSES[0])
    data = admin_data.analytics(snap, now=now + 1)

    item = next(stat for stat in data.items if stat.item_id == 1)
    assert item.attempts == 2
    assert item.correct == 1
    assert item.difficulty == 0.5
    assert item.avg_hints == 1.0
    assert item.avg_time == 20.0
    assert dict(data.hints) == {0: 1, 2: 1}
    assert data.review_total >= 1
    assert data.review_due >= 1
    assert data.review_overdue >= 1
    assert data.review_answered >= 1
    assert data.review_on_time >= 1
    await client.aclose()


async def test_course_page_shows_item_analytics(admin, web_app) -> None:
    """Страница аналитики рисует задания, подсказки и время из снимка."""
    client, user = await _learner(web_app, "Ann")
    conn = _db(web_app, user)
    now = time.time()
    repos.add_event(
        conn,
        Event(source="checked", result=1.0, item_id=1, hints_used=2, time_spent=20.0, ts=now - 10),
    )
    repos.upsert_review(conn, "pandas_structures", 1, now - 2 * 86400)
    conn.commit()

    html = (await admin.get("/admin/course?refresh=1")).text

    assert "Задания" in html
    assert "Подсказки и повторение" in html
    assert "Сложность" in html
    assert "#1" in html
    assert "20 с" in html
    assert "готово сейчас" in html
    await client.aclose()


async def test_course_csv_export(admin, web_app) -> None:
    client, user = await _learner(web_app, "Ann")
    conn = _db(web_app, user)
    now = time.time()
    repos.add_event(
        conn,
        Event(source="checked", result=0.0, item_id=1, hints_used=1, time_spent=5.0, ts=now - 10),
    )
    repos.add_review_log(
        conn,
        "pandas_structures",
        due_at=now - 100,
        answered_at=now - 200,
        passed=True,
        step=1,
    )
    conn.commit()

    response = await admin.get("/admin/course/export.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "тип,модуль,занятие,задание" in response.text
    assert "сложность" in response.text
    assert "занятие" in response.text
    # В выгрузке есть распределение подсказок, повторения и сравнение групп.
    assert "подсказки" in response.text
    assert "уровень 1" in response.text
    assert "повторение" in response.text
    assert "отвечено" in response.text
    assert "группа" in response.text
    await client.aclose()
