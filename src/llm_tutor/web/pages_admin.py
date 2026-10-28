"""Страницы админки: ученики, карточка ученика, аналитика курса.

Доступ — только админам (``admin_page_user``): гость уходит на вход, остальным
раздел отвечает 404. Защита на роутере, чтобы новая страница её не забыла.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from llm_tutor.web import accounts, admin, courses
from llm_tutor.web.accounts import User
from llm_tutor.web.pages import render
from llm_tutor.web.security import NOT_FOUND, accounts_db, admin_page_user

router = APIRouter(dependencies=[Depends(admin_page_user)])

# Учеников на странице списка.
PAGE_SIZE = 50
# Запрос поиска длиннее этого — точно мусор.
MAX_QUERY = 100


def _page_number(raw: str, pages: int) -> int:
    """Номер страницы из запроса: мусор — первая, за границей — последняя."""
    try:
        number = int(raw)
    except ValueError:
        return 1
    return min(max(number, 1), pages)


def _selected_course(course_id: str) -> courses.Course | None:
    """Курс страницы админки: из query, иначе первый опубликованный.

    Курсов теперь может быть много: аналитика и карточка ученика считаются по
    одному, и преподаватель выбирает его в шапке раздела. Если видимых курсов
    нет, страница показывает пустое состояние: скрытый встроенный курс не
    должен просачиваться в ссылки админки.
    """
    chosen = courses.get_course(course_id) if course_id else None
    if chosen is not None and chosen.visible:
        return chosen
    managed = courses.manageable_courses()
    published = [course for course in managed if course.status == "published"]
    if published:
        return published[0]
    if managed:
        # Пока ни один курс не опубликован, аналитика открывает первый
        # черновик: пустая воронка понятнее, чем старый скрытый курс.
        return managed[0]
    return None


def _no_course_page(request: Request, user: User, heading: str, description: str) -> HTMLResponse:
    """Пустое состояние аналитики: считать не по чему, курсов ещё нет."""
    return render(
        request,
        "admin_no_course.html",
        "admin",
        user=user,
        heading=heading,
        description=description,
    )


def _tabs(request: Request, chosen: courses.Course, action: str) -> dict:
    """Контекст вкладок админки: выбор курса и адрес формы выбора."""
    return {
        "admin_courses": courses.manageable_courses(),
        "admin_course": chosen,
        "admin_action": action,
        # Селектор рисуем только на страницах, которые читают ?course=.
        "admin_picker": True,
    }


def _sort_key(account: accounts.AccountRow, snap: admin.Snapshot):
    """Свежая активность — первой; без активности — в конце, новые регистрации выше."""
    row = snap.rows.get(account.id)
    activity = row.last_activity if row is not None else None
    return (activity is None, -(activity or 0.0), -account.created_at, -account.id)


@router.get("/admin")
async def admin_home() -> Response:
    return RedirectResponse("/admin/students", status_code=303)


@router.get("/admin/students", response_class=HTMLResponse)
async def students(
    request: Request,
    q: str = "",
    page: str = "1",
    refresh: str = "",
    course: str = "",
    user: User = Depends(admin_page_user),
) -> HTMLResponse:
    query = q.strip()[:MAX_QUERY]
    now = time.time()
    chosen = _selected_course(course)
    if chosen is None:
        return _no_course_page(request, user, "Ученики", "Кто учится и как далеко прошёл.")
    snap = admin.snapshot(request.app.state, chosen, refresh=refresh not in ("", "0"))
    db = accounts_db(request)
    everyone = accounts.list_users(db)
    found = accounts.list_users(db, query) if query else everyone
    found.sort(key=lambda account: _sort_key(account, snap))
    pages = max(1, -(-len(found) // PAGE_SIZE))
    number = _page_number(page, pages)
    shown = found[(number - 1) * PAGE_SIZE : number * PAGE_SIZE]
    rows = []
    for account in shown:
        row = snap.rows.get(account.id)
        status = row.status if row is not None else "not_enrolled"
        rows.append({"account": account, "row": row, "status": status, "label": admin.STATUS_LABELS[status]})
    return render(
        request,
        "admin_students.html",
        "admin",
        user=user,
        rows=rows,
        q=query,
        page=number,
        pages=pages,
        found=len(found),
        summary=admin.summarize(snap, total=len(everyone), now=now),
        built_at=admin.format_time(snap.built_at),
        format_time=admin.format_time,
        tab="students",
        **_tabs(request, chosen, "/admin/students"),
    )


@router.get("/admin/students/{user_id}", response_class=HTMLResponse)
async def student_card_page(
    request: Request, user_id: int, course: str = "", user: User = Depends(admin_page_user)
) -> HTMLResponse:
    account = accounts.get_account(accounts_db(request), user_id)
    if account is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    chosen = _selected_course(course)
    if chosen is None:
        return _no_course_page(request, user, "Ученик", "Прогресс ученика по выбранному курсу.")
    card = admin.student_card(request.app.state, chosen, account)
    return render(
        request,
        "admin_student.html",
        "admin",
        user=user,
        card=card,
        course=chosen,
        format_time=admin.format_time,
        tab="students",
        **_tabs(request, chosen, f"/admin/students/{user_id}"),
    )


@router.get("/admin/course", response_class=HTMLResponse)
async def course_analytics(
    request: Request, refresh: str = "", course: str = "", user: User = Depends(admin_page_user)
) -> HTMLResponse:
    chosen = _selected_course(course)
    if chosen is None:
        return _no_course_page(
            request, user, "Курс", "Где ученики застревают: воронка по занятиям."
        )
    snap = admin.snapshot(request.app.state, chosen, refresh=refresh not in ("", "0"))
    data, error = courses.try_course_data(chosen)
    return render(
        request,
        "admin_course.html",
        "admin",
        user=user,
        funnel=admin.funnel(snap, data) if data is not None else [],
        broken=error,
        students=len(snap.rows),
        built_at=admin.format_time(snap.built_at),
        tab="course",
        **_tabs(request, chosen, "/admin/course"),
    )
