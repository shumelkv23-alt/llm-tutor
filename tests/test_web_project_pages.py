"""Страницы «Проект»: меню, список курсов, рабочая страница (срез 100)."""

import re
from pathlib import Path

import pytest
from test_web_project import _pass_course, _start, web_llm  # noqa: F401  (фикстура модели)

from llm_tutor.web.app import STATIC_DIR

BASE = "/api/courses/mlcourse/project"
PAGES = ("/project", "/project/mlcourse")


@pytest.fixture
async def ready(student):
    response = await student.post("/api/courses/mlcourse/enroll", json={})
    assert response.status_code == 200
    return student


async def test_nav_has_project(student) -> None:
    html = (await student.get("/")).text
    assert 'href="/project"' in html and ">Проект<" in html
    assert html.index('data-nav="lesson"') < html.index('data-nav="project"') < html.index('data-nav="profile"')


async def test_projects_page(student, web) -> None:
    response = await student.get("/project")
    assert response.status_code == 200
    assert "js/projects.js" in response.text and "css/project.css" in response.text
    assert 'aria-current="page"' in response.text


async def test_projects_page_guest_redirects(web) -> None:
    response = await web.get("/project", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


async def test_project_page_redirects_and_404(student, web_app) -> None:
    """Review Focus: чужой адрес — 404, без записи или без проекта на закрытом курсе — на /project."""
    assert (await student.get("/project/isit-ml")).status_code == 404
    assert (await student.get("/project/no-such-course")).status_code == 404

    response = await student.get("/project/mlcourse", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/project"

    assert (await student.post("/api/courses/mlcourse/enroll", json={})).status_code == 200
    response = await student.get("/project/mlcourse", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/project"

    _pass_course(web_app)
    response = await student.get("/project/mlcourse")
    assert response.status_code == 200
    assert "js/project.js" in response.text and 'data-course="mlcourse"' in response.text


async def test_project_page_open_while_project_runs(ready, web_app) -> None:
    """Начатый проект открывается и когда курс снова «не пройден»."""
    from test_web_project import _set_route

    _pass_course(web_app)
    await _start(ready)
    _set_route(web_app, "ahead")
    assert (await ready.get("/project/mlcourse")).status_code == 200


async def test_project_page_guest_redirects(web) -> None:
    response = await web.get("/project/mlcourse", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


async def test_no_inline_script_or_style(ready, web_app) -> None:
    _pass_course(web_app)
    for path in PAGES:
        html = (await ready.get(path)).text
        scripts = re.findall(r"<script\b[^>]*>", html)
        assert scripts and all("src=" in tag for tag in scripts), path
        assert 'style="' not in html, path


def _source(name: str) -> str:
    return (Path(STATIC_DIR) / "js" / name).read_text(encoding="utf-8")


def test_project_js_renders_proposal_as_text() -> None:
    """Review Focus: предложение наставника и текст ученика — textContent, HTML — только с сервера."""
    code = _source("project.js")
    assert "textContent" in code
    uses = [line.strip() for line in code.splitlines() if "innerHTML" in line and not line.strip().startswith(("//", "*", "/*"))]
    assert len(uses) == 1 and "message.html" in uses[0]


def test_projects_js_has_no_inner_html() -> None:
    code = _source("projects.js")
    assert not [line for line in code.splitlines() if "innerHTML" in line and not line.strip().startswith(("//", "*", "/*"))]


def test_project_css_has_no_external_urls() -> None:
    css = (Path(STATIC_DIR) / "css" / "project.css").read_text(encoding="utf-8")
    assert "http://" not in css and "https://" not in css and "@import" not in css
