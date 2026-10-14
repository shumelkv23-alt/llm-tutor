"""Реестр курсов веб-приложения.

Курс — модули из seed-файлов (граф, банк заданий, анкета модуля) плюс
конспекты теории. У каждого ученика своя БД на каждый курс
(``web/userdb.py``). Курсов два, и у каждого свой каталог модулей: у
mlcourse — общий ``data/`` (``seed_paths=None``), у курса ИСИТ — свой
(``data_dir``); иначе ``course_paths`` смешал бы их в один граф.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from llm_tutor.course.seed import DATA_DIR, course_paths
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
    # Каталог модулей курса (``seed_topicNN.json`` и ``theory/`` внутри).
    data_dir: Path | None = None
    # Показывать ли курс в каталоге. Курс, который ещё пишется, можно держать
    # скрытым: он открыт по прямому адресу, но в списках ученика его нет
    # (``visible_course``). Курс ИСИТ показывается с первого дня — модуль 1
    # выходит по частям, и карточка честно говорит, что занятия готовятся.
    visible: bool = True


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
    Course(
        id="isit-ml",
        title="Python: от основ до машинного обучения",
        subtitle="Python, NumPy, pandas и классический ML",
        description=(
            "Курс по Python: от функций и разбора таблиц в pandas до линейных "
            "моделей, деревьев, ансамблей и метрик на несбалансированных данных. "
            "Материал собирается модуль за модулем."
        ),
        source_url="https://github.com/Eldervald/isit_ml_labs",
        data_dir=DATA_DIR / "isit",
    ),
)


def visible_courses() -> tuple[Course, ...]:
    """Курсы для каталога, главной и списка API — без недописанных."""
    return tuple(course for course in COURSES if course.visible)


def visible_course(course_id: str) -> Course | None:
    """Курс для ученика: недописанный (``visible=False``) — как несуществующий.

    Черновик открыт по прямому адресу (``/lesson/isit-ml``), но в списках
    ученика его нет: иначе он встал бы первым в «Мои курсы» и вытеснил с
    главной настоящий курс, у которого есть занятия (аудит среза 1).
    """
    course = get_course(course_id)
    return course if course is not None and course.visible else None

# Прежние id курсов → нынешние. Записи на курс переносит миграция аккаунтов,
# файл БД ученика переименовывается при открытии, старый адрес занятия
# перенаправляется (срез 52).
RENAMED_COURSES: dict[str, str] = {"mlcourse-topic01": "mlcourse"}

_BY_ID = {course.id: course for course in COURSES}


def get_course(course_id: str) -> Course | None:
    return _BY_ID.get(course_id)


@lru_cache(maxsize=None)
def course_data(course: Course) -> CourseData:
    """Модули курса из seed-файлов (читаются один раз за процесс).

    Правки seed-файлов и новый модуль в каталоге курса видны только после
    перезапуска сервера: кэш держит прочитанный курс целиком.
    """
    if course.seed_paths is not None:
        return load_course_data(list(course.seed_paths))
    if course.data_dir is not None:
        # Glob — при первом обращении, а не на импорте: каталог курса
        # наполняется по мере написания модулей.
        paths = course_paths(course.data_dir)
        if not paths:
            raise FileNotFoundError(f"Нет seed-файлов модулей в {course.data_dir}")
        return load_course_data(paths)
    return load_course_data()
