"""HTML-страницы: главная, мои курсы, занятие, профиль.

Страницы отдают оболочку и стартовые данные; дальше страница говорит с
``/api/*``. Тексты и данные экранирует Jinja2 (автоэкранирование включено).
"""

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

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

_TITLES = {item.key: item.label for item in NAV}


def render(request: Request, template: str, active: str, **context: Any) -> HTMLResponse:
    """Страница в общей оболочке с отмеченным пунктом навигации."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        template,
        {
            "nav": NAV,
            "active": active,
            "page_title": _TITLES[active],
            "user": None,
            **context,
        },
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return render(request, "home.html", "home")


@router.get("/courses", response_class=HTMLResponse)
async def courses(request: Request) -> HTMLResponse:
    return render(request, "courses.html", "courses")


@router.get("/lesson", response_class=HTMLResponse)
async def lesson(request: Request) -> HTMLResponse:
    return render(request, "lesson.html", "lesson")


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request) -> HTMLResponse:
    return render(request, "profile.html", "profile")
