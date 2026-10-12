"""Страницы админки: ученики, карточка ученика, аналитика курса.

Доступ — только админам (``admin_page_user``): гость уходит на вход, остальным
раздел отвечает 404. Защита на роутере, чтобы новая страница её не забыла.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from llm_tutor.web.accounts import User
from llm_tutor.web.pages import render
from llm_tutor.web.security import admin_page_user

router = APIRouter(dependencies=[Depends(admin_page_user)])


@router.get("/admin")
async def admin_home() -> Response:
    return RedirectResponse("/admin/students", status_code=303)


@router.get("/admin/students", response_class=HTMLResponse)
async def students(request: Request, user: User = Depends(admin_page_user)) -> HTMLResponse:
    return render(request, "admin_students.html", "admin", user=user)
