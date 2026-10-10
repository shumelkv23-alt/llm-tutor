"""HTML-страницы: главная, мои курсы, занятие, профиль, вход и регистрация.

Страницы отдают оболочку и стартовые данные; дальше страница говорит с
``/api/*``. Тексты и данные экранирует Jinja2 (автоэкранирование включено).
Главная открыта гостю, остальные разделы — только вошедшему (``page_user``).
"""

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from llm_tutor.web.accounts import User
from llm_tutor.web.security import optional_user, page_user, safe_next

router = APIRouter()


@dataclass(frozen=True)
class NavItem:
    key: str
    href: str
    label: str
    icon: str


NAV: tuple[NavItem, ...] = (
    NavItem("home", "/", "Главная", "home"),
    NavItem("courses", "/courses", "Мои курсы", "book"),
    NavItem("lesson", "/lesson", "Занятие", "lesson"),
    NavItem("profile", "/profile", "Профиль", "user"),
)

_TITLES = {item.key: item.label for item in NAV} | {"login": "Вход", "register": "Регистрация"}


def render(
    request: Request, template: str, active: str, *, user: User | None, **context: Any
) -> HTMLResponse:
    """Страница в общей оболочке с отмеченным пунктом навигации."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        template,
        {
            "nav": NAV,
            "active": active,
            "page_title": _TITLES[active],
            "user": user,
            **context,
        },
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return render(request, "home.html", "home", user=optional_user(request))


@router.get("/courses", response_class=HTMLResponse)
async def courses(request: Request, user: User = Depends(page_user)) -> HTMLResponse:
    return render(request, "courses.html", "courses", user=user)


@router.get("/lesson", response_class=HTMLResponse)
async def lesson(request: Request, user: User = Depends(page_user)) -> HTMLResponse:
    return render(request, "lesson.html", "lesson", user=user)


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: User = Depends(page_user)) -> HTMLResponse:
    return render(request, "profile.html", "profile", user=user)


def _auth_page(request: Request, template: str, active: str, next: str | None) -> Response:
    """Вход и регистрация: вошедшего сразу ведём дальше."""
    target = safe_next(next)
    if optional_user(request) is not None:
        return RedirectResponse(target, status_code=303)
    return render(
        request,
        template,
        active,
        user=None,
        next=target,
        registration_open=request.app.state.settings.registration_open,
    )


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request, next: str | None = None) -> Response:
    return _auth_page(request, "login.html", "login", next)


@router.get("/register", response_class=HTMLResponse)
async def register(request: Request, next: str | None = None) -> Response:
    return _auth_page(request, "register.html", "register", next)
