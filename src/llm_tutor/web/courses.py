"""Реестр курсов веб-приложения.

Курс — граф и банк заданий из seed-файла плюс конспекты теории. У каждого
ученика своя БД на каждый курс (``web/userdb.py``). Сейчас курс один; новый
курс — новая запись здесь и его seed.
"""

from dataclasses import dataclass
from pathlib import Path

from llm_tutor.course.seed import DEFAULT_SEED_PATH


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    subtitle: str
    description: str
    source_url: str
    # Конспекты лежат рядом: data/theory/<имя seed без «seed_»>/ (course.seed).
    seed_path: Path


COURSES: tuple[Course, ...] = (
    Course(
        id="mlcourse-topic01",
        title="Pandas и разведочный анализ данных",
        subtitle="mlcourse.ai · Тема 1",
        description=(
            "Загрузка таблиц, выборка, группировка и сводные таблицы: первичный "
            "анализ данных с pandas. Тьютор объясняет, даёт задания и проверяет код."
        ),
        source_url="https://mlcourse.ai/book/topic01/topic01_intro.html",
        seed_path=DEFAULT_SEED_PATH,
    ),
)

_BY_ID = {course.id: course for course in COURSES}


def get_course(course_id: str) -> Course | None:
    return _BY_ID.get(course_id)
