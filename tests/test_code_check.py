"""Проверка код-заданий запуском (срез N).

Задание считается рабочим, если его тесты падают на заготовке и проходят на
эталонном решении. Проверка идёт отдельным процессом — код пришёл от модели.
"""

import pytest

from llm_tutor.course import store
from llm_tutor.course.build import check, edit
from llm_tutor.web import courses


@pytest.fixture
def course_root(courses_home):
    courses.create_course(title="Основы Python", course_id="python-basics")
    course = courses.get_course("python-basics")
    assert course is not None and course.root is not None
    return course.root


def module_with(items: list[dict]) -> dict:
    """Минимальный модуль из одного занятия с заданными заданиями."""
    return {
        "course": "Основы Python",
        "topic": {
            "number": 1,
            "title": "Модуль",
            "intro": "",
            "published": False,
            "survey": {
                "level_key": "t01_level",
                "level_question": "Что знаешь?",
                "level_options": ["a", "b", "c"],
                "blocks": [
                    {
                        "key": "t01_l1",
                        "title": "Занятие",
                        "question": "Знаешь?",
                        "example": "",
                        "concepts": ["t01_l1"],
                    }
                ],
            },
        },
        "nodes": [
            {
                "id": "t01_l1",
                "name": "Занятие",
                "difficulty": 0.3,
                "topic_id": 1,
                "sections": [{"id": "sec1", "title": "Раздел"}, {"id": "sec2", "title": "Второй"}],
            }
        ],
        "edges": [],
        "items": items,
        "rubrics": [],
        "criteria": [],
        "legacy": {},
    }


def code_item(item_id: int, *, starter: str, tests: str, solution: str | None) -> dict:
    return {
        "id": item_id,
        "prompt": "Посчитай результат",
        "answer_type": "code",
        "concept_weights": {"t01_l1": 1.0},
        "starter": starter,
        "setup": "data = [1, 2, 3]",
        "tests": tests,
        "solution": solution,
        "section": "sec1",
    }


def write(root, items: list[dict]) -> None:
    store.write_json(edit.module_path(root, 1), module_with(items))


# --- запуск задания ---


def test_working_solution_passes():
    ok, reason = check.run_task("data = [1, 2, 3]", "result = sum(data)", "assert result == 6")
    assert ok, reason


def test_failing_solution_is_reported():
    ok, reason = check.run_task("", "result = 0", "assert result == 6")
    assert not ok
    assert "AssertionError" in reason


def test_endless_task_is_stopped():
    ok, reason = check.run_task("", "while True:\n    pass", "", timeout=2.0)
    assert not ok
    assert "секунд" in reason


def test_empty_task_is_not_run():
    ok, reason = check.run_task("", "", "")
    assert not ok and "нет кода" in reason


# --- проверка модуля ---


def test_good_items_have_no_problems(course_root):
    write(
        course_root,
        [
            code_item(1, starter="result = None", tests="assert result == 6", solution="result = sum(data)"),
            code_item(2, starter="total = 0", tests="assert total == 3", solution="total = len(data)"),
        ],
    )
    assert check.check_module(course_root, 1) == []


def test_item_without_solution_is_reported(course_root):
    write(course_root, [code_item(1, starter="result = None", tests="assert result == 6", solution=None)])
    problems = check.check_module(course_root, 1)
    assert [problem.kind for problem in problems] == ["нет эталонного решения"]
    assert "задание 1" in problems[0].as_text()


def test_item_whose_tests_fail_on_solution_is_reported(course_root):
    write(course_root, [code_item(1, starter="result = None", tests="assert result == 6", solution="result = 0")])
    problems = check.check_module(course_root, 1)
    assert [problem.kind for problem in problems] == ["тесты не проходят на решении"]


def test_item_that_passes_on_starter_is_reported(course_root):
    """Тесты, проходящие на заготовке, засчитают задание без работы."""
    write(
        course_root,
        [code_item(1, starter="result = 6", tests="assert result is not None", solution="result = sum(data)")],
    )
    problems = check.check_module(course_root, 1)
    assert [problem.kind for problem in problems] == ["тесты проходят на заготовке"]


def test_non_code_items_are_not_checked(course_root):
    write(
        course_root,
        [
            {
                "id": 5,
                "prompt": "Выбери",
                "answer_type": "choice",
                "options": ["да", "нет"],
                "answer": "0",
                "concept_weights": {"t01_l1": 1.0},
                "section": "sec2",
            }
        ],
    )
    assert check.check_module(course_root, 1) == []


def test_unknown_module_has_nothing_to_check(course_root):
    assert check.check_module(course_root, 7) == []


def test_pandas_task_is_reported_when_library_is_missing(course_root, monkeypatch):
    """Без pandas на сервере задание честно помечается «не проверено»."""
    monkeypatch.setattr(check, "_missing_packages", lambda: ["pandas"])
    write(course_root, [code_item(1, starter="result = None", tests="assert result is None", solution="result = None")])
    problems = check.check_module(course_root, 1)
    assert [problem.kind for problem in problems] == ["не проверено"]
    assert "pandas" in problems[0].detail
