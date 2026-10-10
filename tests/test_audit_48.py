"""Регрессии по находкам ревью среза 48 (вид: сайдбар и тема)."""

import re
from pathlib import Path

import llm_tutor.web

STATIC = Path(llm_tutor.web.__file__).parent / "static"
COURSE = "mlcourse-topic01"


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def test_dark_token_copies_match() -> None:
    """Тёмные токены записаны дважды: выбранная тема и системная. Правятся парно."""
    css = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
    chosen = css[css.index(':root[data-theme="dark"]') :]
    chosen = chosen[: chosen.index("}")]
    system = css[css.index("@media (prefers-color-scheme: dark)") :]
    system = system[system.index(':root:not([data-theme="light"])') :]
    system = system[: system.index("}")]

    assert _tokens(chosen)
    assert _tokens(chosen) == _tokens(system)


async def test_lesson_renders_collapsed_toggle_state(student) -> None:
    """Пока app.js не выполнился, кнопка уже говорит правду об умолчании страницы."""
    await student.post(f"/api/courses/{COURSE}/enroll", json={})
    lesson = (await student.get(f"/lesson/{COURSE}")).text
    home = (await student.get("/")).text

    assert 'aria-expanded="false"' in lesson and "Развернуть меню" in lesson
    assert 'aria-expanded="true"' in home and "Свернуть меню" in home


async def test_profile_renders_system_theme_checked(student) -> None:
    html = (await student.get("/profile")).text

    assert re.search(r'data-theme-choice="system" aria-checked="true"', html)
    assert html.count('aria-checked="true"') == 1


async def test_breadcrumb_separator_is_decorative(student) -> None:
    assert (
        '<span class="sep" aria-hidden="true">/</span>' in (await student.get("/")).text
    )


async def test_auth_pages_are_dark_without_prefs(web) -> None:
    html = (await web.get("/login")).text

    assert 'class="auth-page"' in html
    assert "prefs.js" not in html
