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

import logging
import shutil
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from llm_tutor.course import store
from llm_tutor.course.project_file import PROJECT_FILE, ProjectFileError, load_project_file
from llm_tutor.course.seed import DATA_DIR, SeedError, course_paths, load_course_data
from llm_tutor.course.seed import Course as CourseData
from llm_tutor.schemas import ProjectFile

logger = logging.getLogger(__name__)

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
    # Файл RAG-материалов встроенного курса; ``None`` — общий файл приложения.
    # Курс со своим каталогом модулей не должен читать чужие материалы: номера
    # модулей у курсов совпадают.
    materials_path: Path | None = None


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
    Course(
        id="trees-demo",
        title="Деревья решений и случайный лес",
        subtitle="Демо-курс · один модуль · 5 занятий",
        description=(
            "Показательный мини-курс на один модуль: как дерево решений выбирает "
            "вопросы по энтропии и Джини, как оно строится и почему переобучается, "
            "а затем — как из многих деревьев получается случайный лес. Те же "
            "занятия, задания с проверкой кода и тьютор, что в основном курсе."
        ),
        source_url="https://mlcourse.ai/book/topic03/topic03_decision_trees_kNN.html",
        data_dir=DATA_DIR / "demo",
        materials_path=DATA_DIR / "demo" / "materials.sqlite3",
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


@lru_cache(maxsize=None)
def project_file(course: Course) -> ProjectFile | None:
    """Описания проектов курса (срез 99); ``None`` — у курса нет проекта.

    Файл ``project.json`` лежит рядом с модулями: в папке созданного курса, в
    ``data_dir`` встроенного или в общем ``data/``. Нечитаемый файл или курс не
    роняют сайт: проекта у такого курса просто нет, причина — в логе.
    """
    directory = course.root or course.data_dir or DATA_DIR
    if not (directory / PROJECT_FILE).is_file():
        return None
    try:
        nodes = {node.id for node in course_data(course).nodes}
        return load_project_file(directory, nodes)
    except (ProjectFileError, SeedError, FileNotFoundError):
        logger.exception("Описания проектов курса %s не читаются", course.id)
        return None


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
    project_file.cache_clear()
    refresh_courses()


def folder(course: Course) -> Path:
    """Каталог модулей курса: папка созданного или каталог встроенного.

    У встроенного курса ``root`` нет (он описан кодом), но seed-файлы,
    конспекты и ``course.json`` со счётчиками id лежат в ``data_dir``, а у
    ``mlcourse`` — в общем ``data/``. Правка этих файлов меняет курс у всех
    учеников сразу: отдельной копии не нужно.
    """
    if course.root is not None:
        return course.root
    return course.data_dir or DATA_DIR


def edit_folder(course: Course) -> Path:
    """Каталог, в котором правятся модули встроенного курса (с проверкой)."""
    directory = folder(course)
    if not store.seed_paths(directory):
        raise store.StoreError(f"В курсе {course.id!r} нет файлов модулей — править нечего")
    return directory


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


def copy_course(
    source: Course,
    *,
    title: str = "",
    course_id: str | None = None,
    now: float | None = None,
) -> store.CourseMeta:
    """Заводит редактируемую копию курса — встроенного или созданного.

    Встроенные курсы описаны кодом, поэтому правятся не на диске, а в копии:
    модули, конспекты и описание проекта переезжают в новую папку, дальше
    копия — обычный созданный курс. Оригинал не меняется.
    """
    if _COURSES_DIR is None:
        raise store.StoreError("Каталог курсов не настроен")
    seeds = _source_seeds(source)
    if not seeds:
        raise store.StoreError("В курсе нет модулей — копировать нечего")
    folder = _source_folder(source)
    if course_id and course_id.strip():
        new_id = store.check_id(course_id.strip())
        if get_course(new_id) is not None:
            raise store.StoreError(f"Курс с id {new_id!r} уже есть")
    else:
        new_id = _free_copy_id(_COURSES_DIR, source.id)
    root = store.course_dir(_COURSES_DIR, new_id)
    if root.exists():
        raise store.StoreError(f"Папка {root.name} уже занята — выберите другой id")
    counters = _next_counters(seeds)
    source_meta = meta_for(source)
    moment = time.time() if now is None else now
    try:
        root.mkdir(parents=True)
        for path in seeds:
            if not path.is_file():
                raise store.StoreError(f"Нет файла модуля {path.name}")
            shutil.copy2(path, root / path.name)
        theory = folder / "theory"
        if theory.is_dir():
            shutil.copytree(theory, root / "theory")
        project = folder / PROJECT_FILE
        if project.is_file():
            shutil.copy2(project, root / PROJECT_FILE)
        meta = store.CourseMeta(
            id=new_id,
            title=(title.strip() or f"{source.title} — копия")[:200],
            subtitle=source.subtitle,
            description=source.description,
            source_url=source.source_url,
            status="draft",
            origin=source_meta.origin if source_meta is not None else "manual",
            created_at=moment,
            updated_at=moment,
            next_item_id=counters["item"],
            next_rubric_id=counters["rubric"],
            next_criterion_id=counters["criterion"],
        )
        store.write_meta(root, meta)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    refresh_courses()
    return meta


def _source_folder(course: Course) -> Path:
    """Каталог, где лежат модули, конспекты и проекты курса."""
    if course.root is not None:
        return course.root
    return course.data_dir or DATA_DIR


def _source_seeds(course: Course) -> list[Path]:
    """Seed-файлы копируемого курса."""
    if course.seed_paths is not None:
        return list(course.seed_paths)
    return course_paths(_source_folder(course))


def _copy_id(source_id: str, index: int) -> str:
    """id копии: ``<курс>-copy`` или ``<курс>-copy-N`` без выхода за 40 знаков."""
    suffix = "-copy" if index == 1 else f"-copy-{index}"
    prefix = source_id[: 40 - len(suffix)].rstrip("-")
    return store.check_id(f"{prefix}{suffix}")


def _free_copy_id(courses_dir: Path, source_id: str) -> str:
    """Свободный id копии: первый из ``<id>-copy``, ``<id>-copy-2``, …"""
    for index in range(1, 1000):
        candidate = _copy_id(source_id, index)
        if get_course(candidate) is None and not store.course_dir(courses_dir, candidate).exists():
            return candidate
    raise store.StoreError("Не удалось подобрать id для копии")


def _next_counters(seeds: list[Path]) -> dict[str, int]:
    """Счётчики id копии — следующие после максимальных в её модулях.

    Иначе редактор выдал бы новый id, который уже занят скопированным
    заданием, и правка переписала бы записи журнала ученика.
    """
    next_item = next_rubric = next_criterion = 1
    for path in seeds:
        payload = store.read_json(path)
        for item in payload.get("items") or []:
            next_item = max(next_item, _id_of(item) + 1)
        for rubric in payload.get("rubrics") or []:
            next_rubric = max(next_rubric, _id_of(rubric) + 1)
        for criterion in payload.get("criteria") or []:
            next_criterion = max(next_criterion, _id_of(criterion) + 1)
    return {"item": next_item, "rubric": next_rubric, "criterion": next_criterion}


def _id_of(entry: object) -> int:
    """id записи seed-файла; всё, что не целое число, считается нулём."""
    if not isinstance(entry, dict):
        return 0
    value = entry.get("id")
    return value if isinstance(value, int) and value >= 0 else 0


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


# Счётчики id встроенного курса живут отдельным скрытым файлом рядом с модулями:
# ``course.json`` там занял бы имя метаданных созданного курса и сбил бы реестр.
BUILTIN_IDS_FILE = ".course-ids.json"


def allocate_ids(course_id: str, kind: str, count: int) -> list[int]:
    """Выдаёт новые id курса и сохраняет счётчики (монотонно, без повторов).

    У встроенного курса ``course.json`` нет — счётчики лежат в скрытом файле
    рядом с модулями и начинаются после максимальных id в seed-файлах. Так
    добавление задания в правленый встроенный курс не перепишет журнал.
    """
    course = get_course(course_id)
    if course is None:
        raise store.StoreError(f"Нет курса {course_id!r}")
    if course.root is not None:
        return store.allocate_ids(course.root, kind, count)
    root = edit_folder(course)
    path = root / BUILTIN_IDS_FILE
    try:
        meta = store.CourseMeta.model_validate(store.read_json(path))
    except (store.StoreError, ValidationError):
        meta = _builtin_meta(course, root)
    ids = store.allocate(meta, kind, count)
    store.write_json(path, meta.model_dump())
    return ids


def _builtin_meta(course: Course, root: Path) -> store.CourseMeta:
    """Метаданные встроенного курса — только ради монотонных счётчиков id."""
    counters = _next_counters(list(store.seed_paths(root)))
    return store.CourseMeta(
        id=course.id,
        title=course.title,
        subtitle=course.subtitle,
        description=course.description,
        source_url=course.source_url,
        status="published",
        origin="manual",
        next_item_id=counters["item"],
        next_rubric_id=counters["rubric"],
        next_criterion_id=counters["criterion"],
    )


def _root_of(course_id: str) -> Path:
    """Папка созданного курса; встроенный курс и неизвестный id — ошибка."""
    course = get_course(course_id)
    if course is None or course.root is None:
        raise store.StoreError(f"Нет созданного курса {course_id!r}")
    return course.root
