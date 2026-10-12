"""Реестр курсов веб-приложения.

Курс — модули из seed-файлов (граф, банк заданий, анкета модуля) плюс
конспекты теории. У каждого ученика своя БД на каждый курс
(``web/userdb.py``). Сейчас курс один — mlcourse.ai из пяти модулей; новый
курс — новая запись здесь и его seed-файлы.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from llm_tutor.course.seed import Course as CourseData
from llm_tutor.course.seed import load_course_data


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    subtitle: str
    description: str
    source_url: str
    # Seed-файлы модулей; ``None`` — все модули из data/ (``course_paths``).
    # Конспекты лежат рядом: data/theory/<имя seed без «seed_»>/.
    seed_paths: tuple[Path, ...] | None = None


COURSES: tuple[Course, ...] = (
    Course(
        id="mlcourse",
        title="Машинное обучение: mlcourse.ai",
        subtitle="mlcourse.ai · модули 1–5",
        description=(
            "От разведочного анализа данных с pandas до деревьев, линейных моделей "
            "и случайного леса. Нужен базовый Python: списки, словари, функции. "
            "Тьютор объясняет, даёт задания и проверяет код."
        ),
        source_url="https://mlcourse.ai/book/index.html",
    ),
)

# Прежние id курсов → нынешние. Записи на курс переносит миграция аккаунтов,
# файл БД ученика переименовывается при открытии, старый адрес занятия
# перенаправляется (срез 52).
RENAMED_COURSES: dict[str, str] = {"mlcourse-topic01": "mlcourse"}

_BY_ID = {course.id: course for course in COURSES}


def get_course(course_id: str) -> Course | None:
    return _BY_ID.get(course_id)


@lru_cache(maxsize=None)
def course_data(course: Course) -> CourseData:
    """Модули курса из seed-файлов (читаются один раз за процесс)."""
    paths = None if course.seed_paths is None else list(course.seed_paths)
    return load_course_data(paths)
