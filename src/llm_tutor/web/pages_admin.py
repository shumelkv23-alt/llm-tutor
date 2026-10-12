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
    user: User = Depends(admin_page_user),
) -> HTMLResponse:
    query = q.strip()[:MAX_QUERY]
    now = time.time()
    snap = admin.snapshot(request.app.state, courses.COURSES[0], refresh=refresh not in ("", "0"))
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
    )


@router.get("/admin/students/{user_id}", response_class=HTMLResponse)
async def student_card_page(
    request: Request, user_id: int, user: User = Depends(admin_page_user)
) -> HTMLResponse:
    account = accounts.get_account(accounts_db(request), user_id)
    if account is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    course = courses.COURSES[0]
    card = admin.student_card(request.app.state, course, account)
    return render(
        request,
        "admin_student.html",
        "admin",
        user=user,
        card=card,
        course=course,
        format_time=admin.format_time,
        tab="students",
    )


@router.get("/admin/course", response_class=HTMLResponse)
async def course_analytics(
    request: Request, refresh: str = "", user: User = Depends(admin_page_user)
) -> HTMLResponse:
    course = courses.COURSES[0]
    snap = admin.snapshot(request.app.state, course, refresh=refresh not in ("", "0"))
    return render(
        request,
        "admin_course.html",
        "admin",
        user=user,
        funnel=admin.funnel(snap, courses.course_data(course)),
        students=len(snap.rows),
        built_at=admin.format_time(snap.built_at),
        tab="course",
    )
