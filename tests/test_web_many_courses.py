"""Курсов в вебе стало много: черновики, каталог, выбор курса в админке (Срез B).

Курс, созданный преподавателем, лежит в ``courses_dir``. Пока он черновик,
ученик его не видит ни в каталоге, ни по прямой ссылке; админ видит его в
выборе курса. Бренд в боковом меню — из настроек, а не «mlcourse.ai» в шаблоне.
"""

import pytest

from llm_tutor.web import accounts, courses

ADMIN = {"email": "root@example.com", "name": "Хозяин", "password": "пароль-админа-1"}

# Встроенные курсы реестра (mlcourse и ИСИТ) — созданные идут после них.
BUILTIN_IDS = [course.id for course in courses.COURSES]


async def _sign_up(client, person: dict) -> None:
    response = await client.post("/api/auth/register", json=person)
    assert response.status_code == 201


@pytest.fixture
async def admin(web, web_app):
    """Клиент вошедшего админа."""
    await _sign_up(web, ADMIN)
    user = accounts.find_user(web_app.state.accounts, ADMIN["email"])
    assert user is not None
    accounts.set_admin(web_app.state.accounts, user.id, True)
    return web


def _titles(cards: list[dict]) -> list[str]:
    return [card["title"] for card in cards]


# --- каталог ---


async def test_draft_is_hidden_from_student_catalog(student, make_course) -> None:
    make_course()
    response = await student.get("/api/courses")
    assert response.status_code == 200
    # Прежние встроенные курсы скрыты: каталог пуст, пока нет опубликованных.
    assert _titles(response.json()["courses"]) == [
        course.title for course in courses.visible_courses()
    ]


async def test_published_course_joins_student_catalog(student, make_course) -> None:
    make_course(status="published")
    response = await student.get("/api/courses")
    assert response.status_code == 200
    assert "Основы Python" in _titles(response.json()["courses"])


async def test_draft_course_is_not_found_for_student(student, make_course) -> None:
    make_course()
    for path in ("/api/courses/python-basics/lesson/state", "/lesson/python-basics"):
        response = await student.get(path)
        assert response.status_code == 404, path


async def test_draft_course_cannot_be_enrolled(student, make_course) -> None:
    make_course()
    response = await student.post("/api/courses/python-basics/enroll", json={})
    assert response.status_code == 404


async def test_unpublished_course_leaves_student_navigation(student, make_course, web_app) -> None:
    """Курс сняли с публикации — у ученика он пропадает и из «Занятия»."""
    make_course(status="published")
    assert (await student.post("/api/courses/python-basics/enroll", json={})).status_code == 200
    courses.update_meta("python-basics", status="draft")
    courses.invalidate_course("python-basics")

    # Запись осталась в аккаунте, но курс ученику больше не виден.
    assert "python-basics" in accounts.enrolled_courses(web_app.state.accounts, 1)
    assert (await student.get("/lesson/python-basics")).status_code == 404
    assert (await student.get("/api/courses/python-basics/lesson/state")).status_code == 404


# --- админка ---


async def test_admin_sees_draft_in_course_picker(admin, make_course) -> None:
    make_course()
    page = await admin.get("/admin/students")
    assert page.status_code == 200
    assert "Основы Python" in page.text
    assert "черновик" in page.text


async def test_admin_pages_are_scoped_to_selected_course(admin, make_course) -> None:
    make_course(status="published")
    page = await admin.get("/admin/course?course=python-basics")
    assert page.status_code == 200
    assert 'value="python-basics" selected' in page.text


async def test_unknown_course_falls_back_to_first_published(admin, make_course) -> None:
    make_course(status="published")
    page = await admin.get("/admin/course?course=no-such-course")
    assert page.status_code == 200
    assert 'value="python-basics" selected' in page.text
    # Скрытый прежний курс в выборе не появляется.
    assert 'value="mlcourse"' not in page.text


async def test_admin_analytics_survives_broken_draft(admin, make_course) -> None:
    """Недособранный черновик: страница курса показывает ошибку, а не 500."""
    make_course()
    root = courses.get_course("python-basics")
    assert root is not None and root.root is not None
    (root.root / "seed_topic01.json").write_text('{"topic": 1}', encoding="utf-8")
    courses.invalidate_course("python-basics")

    page = await admin.get("/admin/course?course=python-basics")
    assert page.status_code == 200
    assert "не читаются" in page.text


# --- бренд ---


async def test_brand_caption_comes_from_settings(web, web_app) -> None:
    page = (await web.get("/")).text
    assert web_app.state.settings.app_brand in page
    assert "ТЬЮТОР MLCOURSE.AI" not in page


# --- нечитаемый опубликованный курс (аудит срезов A–B, CRITICAL) ---


def _break_module(course_id: str = "python-basics") -> None:
    """Портит файл модуля у созданного курса — как обрыв записи или правка руками."""
    course = courses.get_course(course_id)
    assert course is not None and course.root is not None
    (course.root / "seed_topic01.json").write_text('{"topic": 1}', encoding="utf-8")
    courses.invalidate_course(course_id)


async def test_broken_published_course_does_not_break_the_site(student, make_course, web) -> None:
    """Один нечитаемый курс не должен ронять каталог, главную и профиль всем."""
    make_course(status="published")
    _break_module()

    for path in ("/", "/courses", "/profile", "/lesson/python-basics", "/api/courses"):
        response = await student.get(path)
        assert response.status_code == 200, path
    # Гостю главная тоже доступна.
    assert (await web.get("/")).status_code == 200


async def test_broken_published_course_is_marked_unavailable(student, make_course) -> None:
    make_course(status="published")
    _break_module()
    cards = (await student.get("/api/courses")).json()["courses"]
    broken = next(card for card in cards if card["id"] == "python-basics")
    assert broken["status"] == "error"
    # Скрытый прежний курс в каталог не подмешивается.
    assert "mlcourse" not in {card["id"] for card in cards}


async def test_enrolling_a_broken_course_does_not_500(student, make_course) -> None:
    make_course(status="published")
    _break_module()
    response = await student.post("/api/courses/python-basics/enroll", json={})
    assert response.status_code == 503


async def test_broken_course_does_not_break_the_admin(admin, make_course) -> None:
    make_course(status="published")
    _break_module()
    for path in ("/admin/students?course=python-basics", "/admin/course?course=python-basics"):
        response = await admin.get(path)
        assert response.status_code == 200, path


# --- курс, папку которого удалили (аудит, MEDIUM) ---


async def test_deleted_course_leaves_the_registry(student, make_course, courses_home) -> None:
    """Папку убрали — курс не должен оставаться призраком до перезапуска."""
    import shutil

    make_course(status="published")
    shutil.rmtree(courses_home / "python-basics")

    assert courses.get_course("python-basics") is None
    assert [
        card["id"] for card in (await student.get("/api/courses")).json()["courses"]
    ] == [course.id for course in courses.visible_courses()]
    # id освободился: курс можно завести заново, не перезапуская сервер.
    courses.create_course(title="Основы Python", course_id="python-basics")


def test_reserved_folder_does_not_break_the_registry(courses_home) -> None:
    """Папка-копия встроенного курса — мусор в каталоге, а не падение реестра."""
    (courses_home / "mlcourse").mkdir(parents=True)
    (courses_home / "mlcourse" / "course.json").write_text('{"id": "mlcourse", "title": "Копия"}', encoding="utf-8")
    courses.configure(courses_home)  # не должно бросить
    assert [course.id for course in courses.all_courses()] == BUILTIN_IDS


async def test_admin_links_keep_the_selected_course(admin, make_course) -> None:
    """Переход по вкладкам не должен молча переключать админку на другой курс."""
    make_course(status="published")
    page = await admin.get("/admin/course?course=python-basics")
    assert 'href="/admin/students?course=python-basics"' in page.text
    assert 'href="/admin/course?course=python-basics"' in page.text
