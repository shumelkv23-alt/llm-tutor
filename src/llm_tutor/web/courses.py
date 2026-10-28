"""Реестр курсов веб-приложения.

Курс — модули из seed-файлов (граф, банк заданий, анкета модуля) плюс
конспекты теории. У каждого ученика своя БД на каждый курс
(``web/userdb.py``).

Встроенные курсы описаны здесь константами: ``mlcourse`` читает модули из
общего ``data/`` (``seed_paths=None``), курс ИСИТ — из своего ``data_dir``;
иначе ``course_paths`` смешал бы их в один граф. Курсы, созданные преподавателем, лежат каждый в своей папке
``<courses_dir>/<id>/`` (``course.store``) и попадают в реестр сканированием:
пока курс в статусе ``draft``, ученики его не видят.
"""

import re
import shutil
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from llm_tutor.course import store
from llm_tutor.course.seed import (
    DATA_DIR,
    SeedError,
    course_paths,
    load_course_data,
    theory_dir_for,
)
from llm_tutor.course.seed import Course as CourseData

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
    # Каталог модулей встроенного курса (``seed_topicNN.json`` и ``theory/``
    # внутри); ``None`` при ``seed_paths=None`` — общий data/.
    data_dir: Path | None = None
    # Показывать ли опубликованный курс в списках ученика. Курс, который ещё
    # пишется, можно держать скрытым: он открыт по прямому адресу, но в
    # каталоге его нет. Курс ИСИТ показывается с первого дня — модуль 1
    # выходит по частям, и карточка честно говорит, что занятия готовятся.
    visible: bool = True


COURSES: tuple[Course, ...] = (
    # Оба прежних курса скрыты из каталога и списков админки: их место заняли
    # два новых курса. Файлы, конспекты и прогресс учеников остаются на месте,
    # и курс по-прежнему открывается по прямому id (на нём лежат тесты движка).
    # Вернуть курс в списки — убрать ``visible=False``.
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
        visible=False,
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
        visible=False,
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


def manageable_courses() -> tuple[Course, ...]:
    """Курсы в списках админки и в выборе курса для аналитики.

    Скрытый курс (``visible=False``) остаётся в реестре и открывается по
    прямому id — на нём лежат прогресс учеников и тесты движка, — но в списках
    его нет, как и у ученика.
    """
    return tuple(course for course in all_courses() if course.visible)


def published_courses() -> tuple[Course, ...]:
    """Курсы, видимые ученикам: без черновиков и без скрытых (``visible=False``)."""
    return tuple(
        course for course in all_courses() if course.status == "published" and course.visible
    )


def visible_courses() -> tuple[Course, ...]:
    """Курсы для каталога, главной и списка API (то же, что ``published_courses``)."""
    return published_courses()


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


def visible_course(course_id: str) -> Course | None:
    """Курс для списков ученика: черновик и скрытый курс — как несуществующие.

    Скрытый курс открыт по прямому адресу (``/lesson/<id>``), но в списках
    ученика его нет: иначе он встал бы первым в «Мои курсы» и вытеснил с
    главной настоящий курс, у которого есть занятия (аудит среза 1 ИСИТ).
    """
    course = student_course(course_id)
    return course if course is not None and course.visible else None


@lru_cache(maxsize=None)
def course_data(course: Course) -> CourseData:
    """Модули курса из seed-файлов (читаются один раз за процесс).

    Ключ кэша — сам неизменяемый ``Course``, поэтому смена id, статуса или
    набора seed-путей даёт другой ключ и свежее чтение. Правку содержимого без
    смены ключа сбрасывает ``invalidate_course``.
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


# Занятия, заведённые редактором: ``t01_lesson3``. Номер выдаёт счётчик курса.
_LESSON_ID_RE = re.compile(r"t\d{2}_lesson(\d+)$")


def _seed_sources(course: Course) -> list[Path]:
    """Seed-файлы курса: у созданного — свои, у встроенного — из его каталога."""
    if course.seed_paths is not None:
        return list(course.seed_paths)
    folder = course.data_dir if course.data_dir is not None else DATA_DIR
    return sorted(course_paths(folder))


def _max_id(seed: dict, key: str) -> int:
    """Наибольший числовой id в списке ``items``/``rubrics``/``criteria``."""
    best = 0
    for entry in seed.get(key) or []:
        ident = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(ident, int) and ident > best:
            best = ident
    return best


def _copied_counters(seeds: list[Path], source: store.CourseMeta | None) -> dict[str, int]:
    """Счётчики копии начинаются за пределами скопированных id.

    Повтор id переписал бы журнал ученика (``repos.replace_seed`` — upsert по
    id), поэтому первая же правка копии не должна выдать id из оригинала.
    """
    limits = {
        "next_item_id": 1,
        "next_rubric_id": 1,
        "next_criterion_id": 1,
        "next_lesson_id": 1,
    }
    pairs = (
        ("items", "next_item_id"),
        ("rubrics", "next_rubric_id"),
        ("criteria", "next_criterion_id"),
    )
    for path in seeds:
        data = store.read_json(path)
        for key, field in pairs:
            limits[field] = max(limits[field], _max_id(data, key) + 1)
        for node in data.get("nodes") or []:
            match = _LESSON_ID_RE.fullmatch(str(node.get("id", ""))) if isinstance(node, dict) else None
            if match is not None:
                limits["next_lesson_id"] = max(limits["next_lesson_id"], int(match.group(1)) + 1)
    if source is not None:
        for field in limits:
            limits[field] = max(limits[field], getattr(source, field))
    return limits


def _copy_identity(source: Course) -> tuple[str, str]:
    """Название и свободный id для копии: «… — копия», «… — копия 2» и т. д."""
    base = f"{source.title} — копия"
    for index in range(1, 100):
        title = base if index == 1 else f"{base} {index}"
        slug = store.slugify(title)
        if index > 1:
            # slugify обрезает до 40 символов, и суффикс « 2» в названии срезался
            # бы вместе с хвостом: дописываем номер к id явно.
            suffix = f"-{index}"
            slug = f"{slug[: 40 - len(suffix)].rstrip('-')}{suffix}"
        root = store.course_dir(_COURSES_DIR, slug)
        if get_course(slug) is None and not (root.exists() and any(root.iterdir())):
            return title, slug
    raise store.StoreError("У курса уже слишком много копий — выберите название вручную")


def copy_course(course_id: str, *, now: float | None = None) -> store.CourseMeta:
    """Копия курса для правки; встроенный курс правится только так.

    Копия — обычный созданный курс-черновик: те же модули и конспекты, но
    отдельная папка, счётчики id выше скопированных и своя БД у учеников.
    """
    if _COURSES_DIR is None:
        raise store.StoreError("Каталог курсов не настроен")
    source = get_course(course_id)
    if source is None:
        raise store.StoreError(f"Нет курса {course_id!r}")
    seeds = _seed_sources(source)
    if not seeds:
        raise store.StoreError("У курса нет модулей — копировать нечего")
    title, new_id = _copy_identity(source)
    root = store.course_dir(_COURSES_DIR, new_id)
    moment = time.time() if now is None else now
    meta = store.CourseMeta(
        id=new_id,
        title=title,
        subtitle=source.subtitle,
        description=source.description,
        source_url=source.source_url,
        status="draft",
        origin="manual",
        created_at=moment,
        updated_at=moment,
        **_copied_counters(seeds, meta_for(source)),
    )
    try:
        root.mkdir(parents=True, exist_ok=True)
        for path in seeds:
            shutil.copy2(path, root / path.name)
            theory = theory_dir_for(path)
            if theory.is_dir():
                shutil.copytree(theory, root / "theory" / path.stem.removeprefix("seed_"))
        if source.root is not None:
            # Материалы и артефакты прогонов — часть курса; БД материалов не
            # копируем: индексы привязаны к каталогу, его соберут заново.
            for name in ("sources", "generation"):
                if (source.root / name).is_dir():
                    shutil.copytree(source.root / name, root / name)
    except OSError as error:
        # Метаданные пишутся последними, поэтому полукопию реестр не увидит;
        # папку всё равно убираем, чтобы не заняла id.
        shutil.rmtree(root, ignore_errors=True)
        raise store.StoreError(f"Не удалось скопировать курс: {error}") from error
    store.write_meta(root, meta)
    refresh_courses()
    return meta


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
