"""Каталог созданного курса: метаданные, счётчики id, атомарная запись.

Курс — это папка ``<courses_dir>/<course_id>/``: метаданные ``course.json``,
seed-файлы модулей (``seed_topicNN.json``), конспекты (``theory/topicNN/``),
источники (``sources/``) и своя БД материалов. Реестр курсов — **сканирование**
этих папок (``scan``): отдельного файла-списка нет, поэтому одна папка — один
атомарный курс, а удаление курса — удаление папки.

Метаданные пишутся последними: папка без ``course.json`` в реестр не попадает,
так что полу-созданный курс не виден вообще.

Слой ядра: ни ``web``, ни модели здесь нет — каталогом пользуются и веб, и
генерация.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

META_NAME = "course.json"

# id курса — он же имя папки и кусок адреса. Только строчная латиница, цифры и
# дефис: так id не станет путём наружу (``..``, ``/``, ``\`` не пройдут).
COURSE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
# Зарезервированные id: встроенный курс и прежние id (``RENAMED_COURSES``).
RESERVED_IDS = frozenset({"mlcourse", "mlcourse-topic01"})

# В модули курса идут только seed-файлы без суффикса-черновика — как в
# ``seed._COURSE_NAME_RE``, но здесь это имена внутри каталога курса.
SEED_NAME_RE = re.compile(r"seed_topic\d{2}\.json")

# Имена устройств Windows (CON, NUL, COM1…): как имя папки они ломают создание
# курса на машине разработки, хотя формально подходят под COURSE_ID_RE.
RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
# Сколько id можно попросить за раз: защита от ``range(10**9)``.
MAX_ALLOCATE = 10_000

# Транслит для id из названия курса (в кириллических названиях id латиницей).
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class StoreError(ValueError):
    """Ошибка работы с каталогом курса (кривой id, нет метаданных)."""


class CourseMeta(BaseModel):
    """Содержимое ``course.json``: кто курс, какой он и сколько id выдано."""

    # Правки полей (статус, счётчики) идут через setattr — пусть pydantic их
    # проверяет: иначе в ``status`` уехало бы что угодно.
    model_config = ConfigDict(validate_assignment=True)

    id: str
    title: str
    subtitle: str = ""
    description: str = ""
    source_url: str = ""
    # Черновик ученикам не виден: каталог, запись на курс и занятие отвечают 404.
    status: Literal["draft", "published"] = "draft"
    # Как курс появился: руками преподавателя или генерацией из материалов.
    origin: Literal["manual", "ai"] = "manual"
    created_at: float = 0.0
    updated_at: float = 0.0
    # Счётчики id курса. Монотонные и общие на весь курс: повтор id переписал бы
    # историю событий ученика (``repos.replace_seed`` делает upsert по id).
    next_item_id: int = Field(default=1, ge=1)
    next_rubric_id: int = Field(default=1, ge=1)
    next_criterion_id: int = Field(default=1, ge=1)

    @field_validator("source_url")
    @classmethod
    def _safe_source_url(cls, value: str) -> str:
        """Ссылка курса уходит в ``href`` карточки — пускаем только http(s).

        Jinja экранирует кавычки, но не схему: ``javascript:`` в href — это
        исполняемый код в сессии ученика.
        """
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("ссылка на источник должна начинаться с http:// или https://")
        return value


_ID_FIELDS = {
    "item": "next_item_id",
    "rubric": "next_rubric_id",
    "criterion": "next_criterion_id",
}


def valid_id(course_id: str) -> bool:
    """Годится ли id для нового курса."""
    return bool(COURSE_ID_RE.fullmatch(course_id)) and course_id not in RESERVED_IDS


def check_id(course_id: str) -> str:
    """Проверенный id курса (``StoreError`` — почему не годится)."""
    if course_id in RESERVED_IDS:
        raise StoreError(f"id {course_id!r} занят встроенным курсом")
    if course_id in RESERVED_NAMES:
        raise StoreError(f"id {course_id!r} — имя устройства Windows, папку так не назвать")
    if not COURSE_ID_RE.fullmatch(course_id):
        raise StoreError(
            "id курса — от 2 до 40 символов: строчная латиница, цифры и дефис, "
            f"начинается с буквы (получено {course_id!r})"
        )
    return course_id


def slugify(title: str) -> str:
    """id из названия курса: транслит, дефисы, обрезка до 40 символов."""
    latin = "".join(_TRANSLIT.get(char, char) for char in title.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:40].strip("-")
    if len(slug) >= 2 and COURSE_ID_RE.fullmatch(slug) and slug not in RESERVED_NAMES:
        return slug
    return "course"


def course_dir(courses_dir: str | Path, course_id: str) -> Path:
    """Папка курса; id проверяется, поэтому путь не уйдёт из ``courses_dir``."""
    return Path(courses_dir) / check_id(course_id)


def seed_paths(root: str | Path) -> tuple[Path, ...]:
    """Seed-файлы модулей курса (черновики и посторонние файлы не берутся)."""
    return tuple(sorted(p for p in Path(root).glob("seed_topic*.json") if SEED_NAME_RE.fullmatch(p.name)))


def atomic_write_text(path: str | Path, text: str) -> None:
    """Запись через временный файл рядом и ``os.replace``.

    Читатель (скан реестра, загрузка курса) не должен увидеть половину файла.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Имя временного файла уникально: два писателя одного файла (правка курса и
    # генерация) иначе затоптали бы друг друга, а на Windows ``os.replace``
    # падает, если цель кто-то держит открытой.
    handle, name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path: str | Path, payload: dict) -> None:
    """JSON на диск атомарно: с переводом строк и отступом, как читает человек."""
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | Path) -> dict:
    """JSON с диска; битый файл или не-объект — ``StoreError`` с именем файла."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise StoreError(f"Нет файла {source}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise StoreError(f"{source.name}: не читается как JSON ({error})") from error
    if not isinstance(payload, dict):
        raise StoreError(f"{source.name}: ожидался объект JSON")
    return payload


def read_meta(root: str | Path) -> CourseMeta:
    """Метаданные курса из ``course.json``."""
    try:
        return CourseMeta.model_validate(read_json(Path(root) / META_NAME))
    except ValidationError as error:
        raise StoreError(f"{META_NAME}: {error}") from error


def write_meta(root: str | Path, meta: CourseMeta) -> None:
    """Метаданные на диск (атомарно)."""
    write_json(Path(root) / META_NAME, meta.model_dump())


def scan(courses_dir: str | Path) -> list[CourseMeta]:
    """Все курсы в каталоге: папка с читаемым ``course.json``.

    Сломанные метаданные не роняют список: такой курс просто не показывается
    (удалять чужие файлы молча нельзя, но и каталог из-за них падать не должен).
    """
    folder = Path(courses_dir)
    if not folder.is_dir():
        return []
    found: list[CourseMeta] = []
    for entry in sorted(folder.iterdir()):
        # Зарезервированные id (mlcourse) пропускаем так же, как битые
        # метаданные: папка-копия встроенного курса не должна ронять реестр.
        if not entry.is_dir() or not valid_id(entry.name):
            continue
        try:
            meta = read_meta(entry)
        except StoreError:
            continue
        if meta.id == entry.name:
            found.append(meta)
    return found


def allocate_ids(root: str | Path, kind: str, count: int) -> list[int]:
    """Выдаёт id курса и сохраняет счётчики (чтение — правка — запись)."""
    meta = read_meta(root)
    ids = allocate(meta, kind, count)
    write_meta(root, meta)
    return ids


def allocate(meta: CourseMeta, kind: str, count: int) -> list[int]:
    """Выдаёт ``count`` новых id и сдвигает счётчик в ``meta`` (запись — на клиенте).

    Монотонность важнее компактности: id, который уже был в курсе, не выдаётся
    повторно никогда — иначе правка курса переписала бы записи журнала ученика.
    """
    field = _ID_FIELDS.get(kind)
    if field is None:
        raise StoreError(f"Неизвестный вид id: {kind!r}")
    if count < 0:
        raise StoreError("Число id не может быть отрицательным")
    if count > MAX_ALLOCATE:
        raise StoreError(f"За раз выдаётся не больше {MAX_ALLOCATE} id (запрошено {count})")
    start = getattr(meta, field)
    setattr(meta, field, start + count)
    return list(range(start, start + count))
