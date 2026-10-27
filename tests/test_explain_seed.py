"""Эталон «Объясни новичку» в seed модуля (срез 85)."""

import json
import shutil

import pytest

from course_fixtures import LESSON_SEED_PATHS, LESSONS_DIR, load_lessons
from llm_tutor.course.seed import SeedError, load_course_data
from llm_tutor.db import repos


def _course_with(tmp_path, number: int, patch) -> object:
    """Курс из копии фикстуры, где ``patch`` правит ``topic.explain`` модуля ``number``."""
    folder = tmp_path / "lessons"
    shutil.copytree(LESSONS_DIR, folder)
    path = folder / f"seed_topic{number:02d}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    patch(data["topic"])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_course_data([folder / p.name for p in LESSON_SEED_PATHS])


def _idea(index: int, lesson: str = "l1_frames", required: bool = False) -> dict:
    return {
        "id": f"idea_{index}",
        "lesson": lesson,
        "text": f"Идея {index}",
        "required": required,
        "expect": f"Ученик говорит про идею {index}",
    }


def _ideas(count: int, required: int = 3) -> list[dict]:
    return [_idea(i, required=i < required) for i in range(count)]


def _set(key: str, value):
    def patch(topic: dict) -> None:
        topic["explain"][key] = value

    return patch


def test_fixture_explain_loads() -> None:
    course = load_course_data(LESSON_SEED_PATHS)
    for topic in course.topics:
        assert topic.explain is not None
        assert len(topic.explain.ideas) == 5


def test_too_few_ideas_is_error(tmp_path) -> None:
    with pytest.raises(SeedError, match="идей"):
        _course_with(tmp_path, 1, _set("ideas", _ideas(4)))


def test_too_many_ideas_is_error(tmp_path) -> None:
    with pytest.raises(SeedError, match="идей"):
        _course_with(tmp_path, 1, _set("ideas", _ideas(9)))


def test_few_required_is_error(tmp_path) -> None:
    with pytest.raises(SeedError, match="обязательных"):
        _course_with(tmp_path, 1, _set("ideas", _ideas(5, required=2)))


def test_few_pitfalls_is_error(tmp_path) -> None:
    def patch(topic: dict) -> None:
        topic["explain"]["pitfalls"] = topic["explain"]["pitfalls"][:1]

    with pytest.raises(SeedError, match="загвоздок"):
        _course_with(tmp_path, 1, patch)


def test_duplicate_ids_is_error(tmp_path) -> None:
    ideas = _ideas(5)
    ideas[4]["id"] = ideas[0]["id"]
    with pytest.raises(SeedError, match="idea_0"):
        _course_with(tmp_path, 1, _set("ideas", ideas))


def test_foreign_lesson_is_error(tmp_path) -> None:
    ideas = _ideas(5)
    ideas[4]["lesson"] = "l2_hist"
    with pytest.raises(SeedError, match="l2_hist"):
        _course_with(tmp_path, 1, _set("ideas", ideas))


def test_foreign_pitfall_lesson_is_error(tmp_path) -> None:
    def patch(topic: dict) -> None:
        topic["explain"]["pitfalls"][0]["lesson"] = "l2_box"

    with pytest.raises(SeedError, match="l2_box"):
        _course_with(tmp_path, 1, patch)


def test_unpublished_without_explain_is_silent(tmp_path) -> None:
    def patch(topic: dict) -> None:
        topic.pop("explain")
        topic["published"] = False

    course = _course_with(tmp_path, 2, patch)
    assert not any("эталона" in warning for warning in course.warnings)


def test_explain_stored_in_db(conn) -> None:
    course = load_lessons(conn)
    assert repos.get_topic(conn, 1).explain == course.topics[0].explain
    assert repos.get_topic(conn, 2).explain == course.topics[1].explain
