"""Страницы админки: ученики, карточка ученика, аналитика курса.

Доступ — только админам (``admin_page_user``): гость уходит на вход, остальным
раздел отвечает 404. Защита на роутере, чтобы новая страница её не забыла.
"""

import csv
import io
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


def _selected_course(course_id: str) -> courses.Course:
    """Курс страницы админки: из query, иначе первый опубликованный.

    Курсов теперь может быть много: аналитика и карточка ученика считаются по
    одному, и преподаватель выбирает его в шапке раздела.
    """
    chosen = courses.get_course(course_id) if course_id else None
    if chosen is not None:
        return chosen
    published = courses.published_courses()
    return published[0] if published else courses.COURSES[0]


def _tabs(request: Request, chosen: courses.Course, action: str) -> dict:
    """Контекст вкладок админки: выбор курса и адрес формы выбора."""
    return {
        "admin_courses": courses.all_courses(),
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
    snap = admin.snapshot(request.app.state, chosen, refresh=refresh not in ("", "0"))
    data, error = courses.try_course_data(chosen)
    return render(
        request,
        "admin_course.html",
        "admin",
        user=user,
        funnel=admin.funnel(snap, data) if data is not None else [],
        analytics=admin.analytics(snap),
        broken=error,
        students=len(snap.rows),
        built_at=admin.format_time(snap.built_at),
        format_time=admin.format_time,
        tab="course",
        **_tabs(request, chosen, "/admin/course"),
    )


@router.get("/admin/course/export.csv")
async def course_export(
    request: Request, course: str = "", user: User = Depends(admin_page_user)
) -> Response:
    """CSV по занятиям и заданиям курса для таблицы преподавателя."""
    chosen = _selected_course(course)
    snap = admin.snapshot(request.app.state, chosen)
    data, _ = courses.try_course_data(chosen)
    blocks = admin.funnel(snap, data) if data is not None else []
    stats = admin.analytics(snap)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "тип",
            "модуль",
            "занятие",
            "задание",
            "дошли",
            "закрыли",
            "слабо",
            "ответов",
            "верно",
            "доля верных",
            "сложность",
            "подсказки",
            "время",
            "различает",
        ]
    )
    for block in blocks:
        for lesson in block.lessons:
            writer.writerow(
                [
                    "занятие",
                    f"Модуль {block.number} · {block.title}",
                    lesson.name,
                    "",
                    lesson.reached,
                    lesson.closed,
                    lesson.weak,
                    "",
                    "",
                    "" if lesson.rate is None else round(lesson.rate, 3),
                    "" if lesson.rate is None else round(1 - lesson.rate, 3),
                    "",
                    "",
                    "",
                ]
            )
    for item in stats.items:
        writer.writerow(
            [
                "задание",
                "",
                "",
                f"#{item.item_id} {item.prompt}",
                "",
                "",
                "",
                item.attempts,
                item.correct,
                "" if item.correct_rate is None else round(item.correct_rate, 3),
                "" if item.difficulty is None else round(item.difficulty, 3),
                item.hints_label,
                item.time_label,
                item.discrimination_label,
            ]
        )
    # Распределение подсказок: сколько ответов дано на каждом уровне.
    if stats.hints:
        writer.writerow([])
        for hints_used, count in stats.hints:
            writer.writerow(
                [
                    "подсказки",
                    "",
                    "Подсказки",
                    f"уровень {hints_used}",
                    "",
                    "",
                    "",
                    count,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
    # Выполнение повторений: те же колонки, чтобы таблица читалась одним листом.
    if stats.review_total or stats.review_answered:
        writer.writerow([])
        for label, value in (
            ("в расписании", stats.review_total),
            ("готово сейчас", stats.review_due),
            ("просрочено больше суток", stats.review_overdue),
        ):
            writer.writerow(
                ["повторение", "", "Повторение", label, value, "", "", "", "", "", "", "", "", ""]
            )
        writer.writerow(
            [
                "повторение",
                "",
                "Повторение",
                "отвечено",
                "",
                "",
                "",
                stats.review_answered,
                stats.review_on_time,
                "" if stats.review_adherence is None else round(stats.review_adherence, 3),
                "",
                "",
                "",
                "",
            ]
        )
    # Сравнение групп: статус, размер и средний прогресс.
    if stats.groups:
        writer.writerow([])
        for group in stats.groups:
            writer.writerow(
                [
                    "группа",
                    "",
                    group.label,
                    "учеников",
                    group.count,
                    round(group.avg_closed, 2),
                    round(group.avg_weak, 2),
                    "",
                    "",
                    round(group.avg_percent / 100, 3),
                    "",
                    "",
                    "",
                    "",
                ]
            )
    response = Response(
        "﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="course.csv"'},
    )
    response.headers["Cache-Control"] = "no-store"
    return response
