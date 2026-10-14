"""Реестр курсов веб-приложения.

Курс — модули из seed-файлов (граф, банк заданий, анкета модуля) плюс
конспекты теории. У каждого ученика своя БД на каждый курс
(``web/userdb.py``).

Встроенный курс ``mlcourse`` описан здесь константой и читает модули из
``data/``. Курсы, созданные преподавателем, лежат каждый в своей папке
``<courses_dir>/<id>/`` (``course.store``) и попадают в реестр сканированием:
пока курс в статусе ``draft``, ученики его не видят.
"""

import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from llm_tutor.course import store
from llm_tutor.course.seed import Course as CourseData
from llm_tutor.course.seed import SeedError, load_course_data

# Курс, который нельзя завести заново: он описан кодом.
BUILTIN_COURSE = "mlcourse"

# Пустой курс — не поломка: его ещё наполняют руками или генерацией.
NO_MODULES = "Модулей ещё нет: добавьте модуль или запустите генерацию из материалов."


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
    # Черновик ученикам не виден (каталог, запись на курс, занятие — 404).
    status: str = "published"
    # Папка созданного курса; ``None`` — встроенный курс из data/.
    root: Path | None = None


COURSES: tuple[Course, ...] = (
    Course(
        id=BUILTIN_COURSE,
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

# Курсы, созданные преподавателем: обновляются сканированием каталога.
#
# Состояние реестра — на процесс, а не на приложение: так его видят и веб, и
# CLI, и пул БД учеников (``userdb``). Значит, в одном процессе живёт один
# реестр — второй ``create_app`` переключит каталог и для первого. В бою процесс
# один; тесты настраивают каталог фикстурой.
_GENERATED: tuple[Course, ...] = ()
_COURSES_DIR: Path | None = None
# id, которых в каталоге не было на момент последней пересборки: иначе каждый
# промах (опечатка в query админки) заново обходил бы диск.
_MISSES: set[str] = set()


def configure(courses_dir: str | Path) -> None:
    """Каталог созданных курсов (зовётся один раз при сборке приложения)."""
    global _COURSES_DIR
    _COURSES_DIR = Path(courses_dir)
    refresh_courses()


def courses_dir() -> Path | None:
    """Каталог созданных курсов (``None`` — приложение его не настраивало)."""
    return _COURSES_DIR


def refresh_courses() -> tuple[Course, ...]:
    """Перечитывает каталог созданных курсов; возвращает их (без встроенных)."""
    global _GENERATED
    _MISSES.clear()
    if _COURSES_DIR is None:
        _GENERATED = ()
        return _GENERATED
    built = []
    for meta in store.scan(_COURSES_DIR):
        root = store.course_dir(_COURSES_DIR, meta.id)
        built.append(
            Course(
                id=meta.id,
                title=meta.title,
                subtitle=meta.subtitle,
                description=meta.description,
                source_url=meta.source_url,
                seed_paths=store.seed_paths(root),
                status=meta.status,
                root=root,
            )
        )
    _GENERATED = tuple(built)
    return _GENERATED


def generated_courses() -> tuple[Course, ...]:
    """Созданные преподавателем курсы (в том числе черновики)."""
    return _GENERATED


def all_courses() -> tuple[Course, ...]:
    """Все курсы: встроенные, затем созданные."""
    return (*COURSES, *_GENERATED)


def published_courses() -> tuple[Course, ...]:
    """Курсы, видимые ученикам (черновики отсеиваются)."""
    return tuple(course for course in all_courses() if course.status == "published")


def _alive(course: Course) -> bool:
    """Курс ещё на диске: папку могли удалить, а реестр помнит её до пересборки."""
    return course.root is None or (course.root / store.META_NAME).is_file()


def get_course(course_id: str) -> Course | None:
    """Курс по id; неизвестный — перечитать каталог (файл мог появиться только что)."""
    builtin = _BY_ID.get(course_id)
    if builtin is not None:
        return builtin
    found = next((course for course in _GENERATED if course.id == course_id), None)
    if found is not None and _alive(found):
        return found
    if course_id in _MISSES:
        return None  # уже искали с прошлой пересборки каталога
    for course in refresh_courses():
        if course.id == course_id:
            return course
    _MISSES.add(course_id)
    return None


def student_course(course_id: str, *, include_drafts: bool = False) -> Course | None:
    """Курс глазами ученика: черновик для него не существует.

    ``include_drafts`` — для админа: ему черновик нужен, чтобы пройти занятие
    до публикации и увидеть его глазами ученика.
    """
    course = get_course(course_id)
    if course is None:
        return None
    if course.status != "published" and not include_drafts:
        return None
    return course


@lru_cache(maxsize=None)
def course_data(course: Course) -> CourseData:
    """Модули курса из seed-файлов (читаются один раз за процесс).

    Ключ кэша — сам неизменяемый ``Course``, поэтому смена id, статуса или
    набора seed-путей даёт другой ключ и свежее чтение. Правку содержимого без
    смены ключа сбрасывает ``invalidate_course``.
    """
    paths = None if course.seed_paths is None else list(course.seed_paths)
    return load_course_data(paths)


def try_course_data(course: Course) -> tuple[CourseData | None, str | None]:
    """Модули курса без падения: ``(данные, беда)``.

    Недособранный черновик валидацию не проходит — это нормальное его
    состояние, а не сбой: страницы админки показывают беду текстом.
    """
    if course.root is not None and not course.seed_paths:
        return None, NO_MODULES
    try:
        return course_data(course), None
    except (SeedError, FileNotFoundError) as error:
        return None, f"Модули курса не читаются: {error}"


def invalidate_course(course_id: str | None = None) -> None:
    """Сбрасывает разбор курса после правки файлов и перечитывает каталог.

    ``course_id`` — для читателя: ``lru_cache`` чистится целиком (разбор курса
    стоит миллисекунды, а выборка по ключу потребовала бы своего кэша).
    """
    course_data.cache_clear()
    refresh_courses()


def meta_for(course: Course) -> store.CourseMeta | None:
    """Метаданные созданного курса (``None`` — курс встроенный)."""
    if course.root is None:
        return None
    try:
        return store.read_meta(course.root)
    except store.StoreError:
        return None


def create_course(
    *,
    title: str,
    course_id: str | None = None,
    subtitle: str = "",
    description: str = "",
    source_url: str = "",
    origin: str = "manual",
    now: float | None = None,
) -> store.CourseMeta:
    """Заводит пустой курс: папка с одними метаданными.

    Курс без модулей в каталоге ученика не появится (он черновик), но уже
    виден преподавателю — туда и грузятся материалы, и оттуда запускается
    генерация.
    """
    if _COURSES_DIR is None:
        raise store.StoreError("Каталог курсов не настроен")
    title = title.strip()
    if not title:
        raise store.StoreError("Укажите название курса")
    course_id = store.check_id(course_id.strip() if course_id and course_id.strip() else store.slugify(title))
    if get_course(course_id) is not None:
        raise store.StoreError(f"Курс с id {course_id!r} уже есть")
    root = store.course_dir(_COURSES_DIR, course_id)
    if root.exists() and any(root.iterdir()):
        raise store.StoreError(f"Папка {root.name} уже занята — выберите другой id")
    moment = time.time() if now is None else now
    try:
        meta = store.CourseMeta(
            id=course_id,
            title=title,
            subtitle=subtitle.strip(),
            description=description.strip(),
            source_url=source_url,
            status="draft",
            origin="ai" if origin == "ai" else "manual",
            created_at=moment,
            updated_at=moment,
        )
    except ValidationError as error:
        raise store.StoreError(str(error)) from error
    store.write_meta(root, meta)
    refresh_courses()
    return meta


def save_meta(course_id: str, meta: store.CourseMeta, *, now: float | None = None) -> Course:
    """Записывает метаданные курса и обновляет реестр; возвращает курс."""
    course = get_course(course_id)
    if course is None or course.root is None:
        raise store.StoreError(f"Нет созданного курса {course_id!r}")
    if meta.id != course_id:
        # Иначе метаданные уехали бы с чужим id, реестр такую папку пропускает —
        # и курс молча исчез бы из списка.
        raise store.StoreError(f"В метаданных id {meta.id!r}, а курс — {course_id!r}")
    meta.updated_at = time.time() if now is None else now
    store.write_meta(course.root, meta)
    refresh_courses()
    updated = get_course(course_id)
    assert updated is not None  # метаданные только что записаны
    return updated


def update_meta(course_id: str, **fields: str) -> store.CourseMeta:
    """Меняет поля метаданных (только переданные) и пишет их на диск."""
    root = _root_of(course_id)
    meta = store.read_meta(root)
    for key, value in fields.items():
        if key not in store.CourseMeta.model_fields:
            raise store.StoreError(f"Неизвестное поле метаданных: {key!r}")
        if key == "id":
            raise store.StoreError("id курса не меняется")
        if key == "title" and isinstance(value, str) and not value.strip():
            raise store.StoreError("У курса должно быть название")
        try:
            setattr(meta, key, value.strip() if isinstance(value, str) else value)
        except ValidationError as error:
            raise store.StoreError(f"{key}: {error}") from error
    store.write_meta(root, meta)
    refresh_courses()
    return meta


def allocate_ids(course_id: str, kind: str, count: int) -> list[int]:
    """Выдаёт новые id курса и сохраняет счётчики (монотонно, без повторов)."""
    return store.allocate_ids(_root_of(course_id), kind, count)


def _root_of(course_id: str) -> Path:
    """Папка созданного курса; встроенный курс и неизвестный id — ошибка."""
    course = get_course(course_id)
    if course is None or course.root is None:
        raise store.StoreError(f"Нет созданного курса {course_id!r}")
    return course.root
