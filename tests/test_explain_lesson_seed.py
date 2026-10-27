"""Эталон «Объясни новичку» на занятие: формат, проверки, БД (срез 92)."""

import json
import shutil

import pytest

from course_fixtures import LESSON_SEED_PATHS, LESSONS_DIR, load_lessons
from llm_tutor.course.seed import SeedError, load_course_data
from llm_tutor.db import repos
from llm_tutor.schemas import ExplainState

IMPORTANT = {"l1_select", "l1_group", "l2_box", "l2_corr"}


def _course_with(tmp_path, number: int, node_id: str, patch) -> object:
    """Курс из копии фикстуры, где ``patch`` правит ``explain`` узла ``node_id``."""
    folder = tmp_path / "lessons"
    if not folder.exists():
        shutil.copytree(LESSONS_DIR, folder)
    path = folder / f"seed_topic{number:02d}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    node = next(node for node in data["nodes"] if node["id"] == node_id)
    patch(node)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_course_data([folder / p.name for p in LESSON_SEED_PATHS])


def _idea(index: int, required: bool = False) -> dict:
    return {
        "id": f"idea_{index}",
        "text": f"Идея {index}",
        "required": required,
        "expect": f"Ученик говорит про идею {index}",
    }


def _set(key: str, value):
    def patch(node: dict) -> None:
        node["explain"][key] = value

    return patch


def _nodes(course) -> dict:
    return {node.id: node for node in course.nodes}


def test_fixture_lessons_have_explain() -> None:
    course = load_course_data(LESSON_SEED_PATHS)
    for node in course.nodes:
        if node.id in IMPORTANT:
            assert node.explain is not None, node.id
            for entry in [*node.explain.ideas, *node.explain.pitfalls]:
                assert entry.lesson == node.id
        else:
            assert node.explain is None, node.id


@pytest.mark.parametrize("count", [1, 5])
def test_lesson_ideas_bounds(tmp_path, count: int) -> None:
    ideas = [_idea(i, required=i == 0) for i in range(count)]
    with pytest.raises(SeedError, match="идей"):
        _course_with(tmp_path, 1, "l1_group", _set("ideas", ideas))


def test_lesson_needs_required(tmp_path) -> None:
    ideas = [_idea(i) for i in range(3)]
    with pytest.raises(SeedError, match="обязательн"):
        _course_with(tmp_path, 1, "l1_group", _set("ideas", ideas))


@pytest.mark.parametrize("count", [0, 3])
def test_lesson_pitfalls_bounds(tmp_path, count: int) -> None:
    pitfalls = [
        {"id": f"pit_{i}", "belief": f"Это так {i}?", "fix": f"Нет {i}"} for i in range(count)
    ]
    with pytest.raises(SeedError, match="загвоздок"):
        _course_with(tmp_path, 1, "l1_group", _set("pitfalls", pitfalls))


def test_belief_must_be_question(tmp_path) -> None:
    def patch(node: dict) -> None:
        node["explain"]["pitfalls"][0]["id"] = "not_a_question"
        node["explain"]["pitfalls"][0]["belief"] = "Это не вопрос."

    with pytest.raises(SeedError, match="not_a_question"):
        _course_with(tmp_path, 1, "l1_group", patch)


def test_duplicate_ids_in_module(tmp_path) -> None:
    """Один id в двух занятиях модуля: ответы и отчёты спутались бы."""
    folder = tmp_path / "lessons"
    shutil.copytree(LESSONS_DIR, folder)
    path = folder / "seed_topic01.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in data["nodes"]}
    shared = nodes["l1_select"]["explain"]["ideas"][0]["id"]
    nodes["l1_group"]["explain"]["ideas"][0]["id"] = shared
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SeedError, match=shared):
        load_course_data([folder / p.name for p in LESSON_SEED_PATHS])


def test_foreign_lesson_field(tmp_path) -> None:
    def patch(node: dict) -> None:
        node["explain"]["ideas"][0]["lesson"] = "l1_frames"

    with pytest.raises(SeedError, match="l1_frames"):
        _course_with(tmp_path, 1, "l1_group", patch)


def _strip_all_explain(tmp_path, number: int, *, published: bool = True):
    """Курс, где в модуле ``number`` нет ни модульного, ни занятийных эталонов."""
    folder = tmp_path / "lessons"
    shutil.copytree(LESSONS_DIR, folder)
    path = folder / f"seed_topic{number:02d}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["topic"].pop("explain", None)
    data["topic"]["published"] = published
    for node in data["nodes"]:
        node.pop("explain", None)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_course_data([folder / p.name for p in LESSON_SEED_PATHS])


def test_module_without_important_lessons_warns(tmp_path) -> None:
    course = _strip_all_explain(tmp_path, 2)
    assert "В модуле 2 нет важных занятий («Объясни новичку»)" in course.warnings


def test_unpublished_module_without_important_lessons_is_silent(tmp_path) -> None:
    course = _strip_all_explain(tmp_path, 2, published=False)
    assert not any("важных занятий" in warning for warning in course.warnings)


def test_explain_reaches_db(conn) -> None:
    course = load_lessons(conn)
    configs = repos.get_explain_configs(conn)
    assert set(configs) == IMPORTANT
    for node in course.nodes:
        if node.id in IMPORTANT:
            assert configs[node.id] == node.explain


def test_session_kind_and_lesson(conn) -> None:
    load_lessons(conn)
    required = repos.create_explain_session(
        conn, 1, now=1.0, concept_id="l1_group", kind="required"
    )
    session = repos.get_explain_session(conn, required)
    assert (session.concept_id, session.kind) == ("l1_group", "required")
    assert repos.finished_required(conn) == set()

    repos.update_explain_session(
        conn, required, state=ExplainState(), updated_at=2.0, status="finished", commit=True
    )
    assert repos.finished_required(conn) == {"l1_group"}

    voluntary = repos.create_explain_session(conn, 1, now=3.0, concept_id="l1_select")
    repos.update_explain_session(
        conn, voluntary, state=ExplainState(), updated_at=4.0, status="finished", commit=True
    )
    assert repos.get_explain_session(conn, voluntary).kind == "voluntary"
    assert repos.finished_required(conn) == {"l1_group"}
