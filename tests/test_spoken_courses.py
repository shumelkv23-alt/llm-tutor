"""Голосовые задания в опубликованных курсах: по одному на модуль.

Задание «spoken» проверяется рубрикой, как открытый ответ: браузер (или
сервер) превращает речь в текст, дальше всё как обычно. Здесь — данные:
шесть заданий в каждом курсе, у каждого своя рубрика с критериями и место
в разделе занятия.
"""

from pathlib import Path

import pytest

from llm_tutor.course.seed import course_paths, load_course_data

COURSES = Path(__file__).resolve().parents[1] / "data" / "courses"
PUBLISHED = ["python-mastery", "big-data-minimum"]


@pytest.mark.parametrize("course_id", PUBLISHED)
def test_spoken_item_in_every_module(course_id: str) -> None:
    course = load_course_data(course_paths(COURSES / course_id))
    spoken = [item for item in course.items if item.answer_type == "spoken"]

    assert len(spoken) == len(course.topics) == 6
    lessons = [next(iter(item.concept_weights)) for item in spoken]
    assert len(set(lessons)) == 6, "голосовые задания должны быть в разных модулях"
    for item in spoken:
        assert item.section is not None, "голосовое задание живёт в разделе занятия"
        assert not item.final
        assert item.options == []
        assert item.answer is None


@pytest.mark.parametrize("course_id", PUBLISHED)
def test_spoken_items_are_gradable_by_rubric(course_id: str) -> None:
    course = load_course_data(course_paths(COURSES / course_id))
    rubrics = {rubric.id: rubric for rubric in course.rubrics}

    for item in (entry for entry in course.items if entry.answer_type == "spoken"):
        assert item.rubric_id is not None
        assert item.rubric_id in rubrics
        criteria = [entry for entry in course.criteria if entry.rubric_id == item.rubric_id]
        assert len(criteria) >= 3, f"у задания {item.id} мало критериев"
        assert all(entry.criterion.strip() for entry in criteria)


@pytest.mark.parametrize("course_id", PUBLISHED)
def test_spoken_items_do_not_repeat_rubrics(course_id: str) -> None:
    """Своя рубрика на задание: правка одного не меняет проверку другого."""
    course = load_course_data(course_paths(COURSES / course_id))
    spoken = [item for item in course.items if item.answer_type == "spoken"]

    assert len({item.rubric_id for item in spoken}) == len(spoken)
