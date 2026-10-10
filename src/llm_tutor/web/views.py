"""Данные страниц и API из ядра: словари для шаблонов и JSON, без HTML."""

from llm_tutor.config import Settings
from llm_tutor.core.overview import course_progress
from llm_tutor.web.courses import Course
from llm_tutor.web.userdb import UserDBPool

STATUS_LABELS = {
    "available": "Не начат",
    "survey": "Анкета",
    "in_progress": "В процессе",
    "done": "Пройден",
}


def course_card(
    course: Course,
    *,
    user_id: int,
    enrolled: bool,
    pool: UserDBPool,
    settings: Settings,
) -> dict:
    """Карточка курса: прогресс читается из БД ученика, только если он записан."""
    card = {
        "id": course.id,
        "title": course.title,
        "subtitle": course.subtitle,
        "description": course.description,
        "source_url": course.source_url,
        "href": f"/lesson/{course.id}",
        "enrolled": enrolled,
        "topics": None,
        "closed": 0,
        "current_topic": None,
        "status": "available",
    }
    if enrolled:
        progress = course_progress(pool.open(user_id, course.id), settings=settings)
        card.update(topics=progress.total, closed=progress.closed, current_topic=progress.current_name)
        if not progress.survey_done:
            card["status"] = "survey"
        elif progress.closed >= progress.total:
            card["status"] = "done"
        else:
            card["status"] = "in_progress"
    else:
        card["topics"] = _topic_count(course)
    card["status_label"] = STATUS_LABELS[card["status"]]
    card["percent"] = round(100 * card["closed"] / card["topics"]) if card["topics"] else 0
    return card


def _topic_count(course: Course) -> int:
    """Число тем курса из seed — без создания БД ученика."""
    from llm_tutor.course.seed import load_seed_data

    return len(load_seed_data(course.seed_path).nodes)
