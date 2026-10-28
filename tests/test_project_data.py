"""Проект по курсу: описания, шаблон этапов, схема и репозитории (срез 97)."""

import json

import pytest

from llm_tutor.course.project_file import PROJECT_FILE, ProjectFileError, load_project_file
from llm_tutor.course.seed import DATA_DIR, load_course_data
from llm_tutor.db import repos
from llm_tutor.db.connection import SCHEMA_VERSION
from llm_tutor.schemas import ProjectBrief, ProjectState
from llm_tutor.student.project import TEMPLATES

NODES = {"eda_case", "validation", "forest"}


def _brief(**fields) -> dict:
    brief = {
        "id": "churn",
        "title": "Прогноз оттока",
        "summary": "Предскажи уход клиентов.",
        "result": "Модель лучше базовой.",
        "lessons": ["eda_case"],
        "datasets": [{"title": "Telco", "url": "https://example.com/telco"}],
    }
    brief.update(fields)
    return brief


def _write(tmp_path, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    (tmp_path / PROJECT_FILE).write_text(text, encoding="utf-8")


def test_mlcourse_briefs_load() -> None:
    project = load_project_file(DATA_DIR, {node.id for node in load_course_data().nodes})
    assert project is not None and project.template == "ml"
    assert [brief.id for brief in project.briefs] == ["churn", "bikes", "own"]
    assert all(brief.datasets for brief in project.briefs)


def test_demo_briefs_load() -> None:
    """У демо-курса свой проект: занятия описаний — занятия демо-курса."""
    demo = load_course_data(sorted((DATA_DIR / "demo").glob("seed_topic*.json")))
    project = load_project_file(DATA_DIR / "demo", {node.id for node in demo.nodes})
    assert project is not None and project.template == "ml"
    assert [brief.id for brief in project.briefs] == ["churn_forest", "own_trees"]
    assert all(brief.datasets for brief in project.briefs)


def test_project_courses() -> None:
    """Проект есть у mlcourse и демо-курса; у isit-ml его нет."""
    assert (DATA_DIR / PROJECT_FILE).exists()
    assert (DATA_DIR / "demo" / PROJECT_FILE).exists()
    assert not (DATA_DIR / "isit" / PROJECT_FILE).exists()


def test_missing_file_is_none(tmp_path) -> None:
    assert load_project_file(tmp_path, NODES) is None


BAD = {
    "broken_json": "{",
    "unknown_template": {"template": "web", "briefs": [_brief()]},
    "no_briefs": {"template": "ml", "briefs": []},
    "four_briefs": {"template": "ml", "briefs": [_brief(id=f"b{i}") for i in range(4)]},
    "duplicate_id": {"template": "ml", "briefs": [_brief(), _brief()]},
    "unknown_lesson": {"template": "ml", "briefs": [_brief(lessons=["no_such"])]},
    "bad_url": {
        "template": "ml",
        "briefs": [_brief(datasets=[{"title": "x", "url": "javascript:alert(1)"}])],
    },
    "short_title": {"template": "ml", "briefs": [_brief(title="ab")]},
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_bad_file_is_error(tmp_path, case) -> None:
    _write(tmp_path, BAD[case])
    with pytest.raises(ProjectFileError, match="project.json"):
        load_project_file(tmp_path, NODES)


def test_ml_template_shape() -> None:
    ml = TEMPLATES["ml"]
    assert [stage.id for stage in ml.stages] == ["data", "baseline", "improve", "conclusions"]
    assert [field.key for field in ml.card] == ["title", "problem", "data", "success"]
    assert all(field.required for field in ml.card)
    required = {stage.id: {f.key for f in stage.fields if f.required} for stage in ml.stages}
    assert required == {
        "data": {"done", "findings"},
        "baseline": {"model", "metric"},
        "improve": {"tried", "result"},
        "conclusions": {"answer", "limits"},
    }
    for stage in ml.stages:
        keys = [field.key for field in stage.fields]
        assert len(keys) == len(set(keys)), stage.id
        for field in stage.fields:
            assert field.max_chars == (8000 if field.key == "code" else 4000)
        assert stage.goal and stage.checklist


def test_project_repos(conn) -> None:
    brief = ProjectBrief.model_validate(_brief())
    first = repos.create_project(conn, brief=brief, template="ml", now=1.0, commit=True)
    project = repos.get_project(conn, first)
    assert (project.status, project.brief, project.state) == ("idea", brief, ProjectState())
    assert repos.current_project(conn).id == first

    repos.add_project_turn(conn, first, "mentor", "Привет", ts=2.0, commit=True)
    turn_id = repos.add_project_turn(
        conn, first, "mentor", "Предлагаю", ts=3.0, meta={"proposal": {"title": "X"}}, commit=True
    )
    turns = repos.get_project_turns(conn, first)
    assert [turn.content for turn in turns] == ["Привет", "Предлагаю"]
    assert repos.get_project_turn(conn, turn_id).meta == {"proposal": {"title": "X"}}

    repos.update_project(
        conn, first, state=ProjectState(), updated_at=4.0, status="archived", commit=True
    )
    assert repos.current_project(conn) is None
    second = repos.create_project(conn, brief=brief, template="ml", now=5.0, commit=True)
    assert repos.current_project(conn).id == second


def test_schema_version(conn) -> None:
    assert SCHEMA_VERSION == 10
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "project_turns"} <= tables
