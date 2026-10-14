"""Реестр курсов: каталог созданных курсов, черновики, счётчики id.

Курс, созданный преподавателем, — папка в ``courses_dir``. Пока метаданных нет
или курс в статусе ``draft``, ученик его не видит; встроенный ``mlcourse``
остаётся описанным кодом и читает модули из ``data/``.
"""

import json
import shutil
from pathlib import Path

import pytest

from course_fixtures import LESSONS_DIR
from llm_tutor.course import store
from llm_tutor.web import courses


def write_course(root: Path, course_id: str = "python-basics", **fields) -> Path:
    """Папка курса с метаданными — как их пишет ``create_course``."""
    folder = root / course_id
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"id": course_id, "title": "Основы Python", **fields}
    (folder / store.META_NAME).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return folder


def copy_module(root: Path, course_id: str, seed_name: str = "seed_topic01.json") -> Path:
    """Копирует настоящий модуль фикстуры вместе с конспектами в курс."""
    source = LESSONS_DIR / seed_name
    target = root / course_id / seed_name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, target)
    shutil.copytree(LESSONS_DIR / "theory" / seed_name.removeprefix("seed_").removesuffix(".json"),
                    target.parent / "theory" / seed_name.removeprefix("seed_").removesuffix(".json"))
    return target


# --- store: id, слаг, атомарная запись ---


def test_slugify_transliterates_cyrillic():
    assert store.slugify("Основы Python") == "osnovy-python"


def test_slugify_falls_back_for_unusable_title():
    assert store.slugify("!!!") == "course"


def test_check_id_rejects_reserved_and_traversal():
    for bad in ("mlcourse", "mlcourse-topic01", "../evil", "Upper", "a", "9lives"):
        with pytest.raises(store.StoreError):
            store.check_id(bad)


def test_course_dir_stays_inside_courses_dir(tmp_path):
    assert store.course_dir(tmp_path, "python-basics") == tmp_path / "python-basics"
    with pytest.raises(store.StoreError):
        store.course_dir(tmp_path, "../evil")


def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "course.json"
    store.atomic_write_text(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"
    assert [p.name for p in tmp_path.iterdir()] == ["course.json"]


# --- создание курса ---


def test_create_course_writes_draft_meta(courses_home):
    meta = courses.create_course(title="Основы Python", now=1000.0)
    assert meta.id == "osnovy-python"
    assert meta.status == "draft"
    assert meta.origin == "manual"
    assert meta.created_at == 1000.0
    assert store.read_meta(courses_home / "osnovy-python").title == "Основы Python"


def test_create_course_rejects_duplicate_id(courses_home):
    courses.create_course(title="Основы Python")
    with pytest.raises(store.StoreError):
        courses.create_course(title="Основы Python")


def test_create_course_rejects_empty_title(courses_home):
    with pytest.raises(store.StoreError):
        courses.create_course(title="   ")


def test_create_course_rejects_busy_folder(courses_home):
    (courses_home / "manual-course").mkdir(parents=True)
    (courses_home / "manual-course" / "seed_topic01.json").write_text("{}", encoding="utf-8")
    with pytest.raises(store.StoreError):
        courses.create_course(title="Manual Course", course_id="manual-course")


# --- сканирование ---


def test_scan_skips_dirs_without_or_with_broken_meta(courses_home):
    write_course(courses_home, "good-course")
    (courses_home / "no-meta").mkdir()
    (courses_home / "broken").mkdir()
    (courses_home / "broken" / store.META_NAME).write_text("{не json", encoding="utf-8")
    (courses_home / "not-a-course.txt").write_text("x", encoding="utf-8")
    assert [meta.id for meta in store.scan(courses_home)] == ["good-course"]


def test_scan_skips_meta_of_another_course(courses_home):
    """Метаданные чужого id в папке — курс не подхватывается под чужим именем."""
    write_course(courses_home, "python-basics", id="another-course")
    assert store.scan(courses_home) == []


def test_generated_course_gets_root_and_seed_paths(courses_home):
    write_course(courses_home)
    copy_module(courses_home, "python-basics")
    courses.refresh_courses()
    course = courses.get_course("python-basics")
    assert course is not None
    assert course.root == courses_home / "python-basics"
    assert course.seed_paths == (courses_home / "python-basics" / "seed_topic01.json",)


def test_generated_seeds_do_not_leak_into_builtin_course(courses_home):
    """Модули созданного курса не попадают в mlcourse: у него свой каталог."""
    write_course(courses_home)
    copy_module(courses_home, "python-basics")
    courses.refresh_courses()
    builtin = courses.get_course("mlcourse")
    assert builtin is not None
    assert builtin.seed_paths is None  # по-прежнему «все модули из data/»
    assert all(course.root != courses_home for course in courses.COURSES)


# --- черновик и публикация ---


def test_draft_is_hidden_from_students_but_visible_in_registry(courses_home):
    courses.create_course(title="Основы Python")
    assert [course.id for course in courses.all_courses()] == ["mlcourse", "osnovy-python"]
    assert [course.id for course in courses.published_courses()] == ["mlcourse"]


def test_publishing_makes_course_visible(courses_home):
    courses.create_course(title="Основы Python")
    courses.update_meta("osnovy-python", status="published", subtitle="Вводный")
    assert [course.id for course in courses.published_courses()] == ["mlcourse", "osnovy-python"]
    course = courses.get_course("osnovy-python")
    assert course is not None and course.subtitle == "Вводный"


def test_update_meta_rejects_unknown_field_and_bad_status(courses_home):
    courses.create_course(title="Основы Python")
    with pytest.raises(store.StoreError):
        courses.update_meta("osnovy-python", nonsense="x")
    with pytest.raises(store.StoreError):
        courses.update_meta("osnovy-python", status="almost-published")
    with pytest.raises(store.StoreError):
        courses.update_meta("osnovy-python", id="renamed")


def test_update_meta_refuses_builtin_course():
    with pytest.raises(store.StoreError):
        courses.update_meta("mlcourse", title="Другое")


def test_meta_for_builtin_is_none():
    builtin = courses.get_course("mlcourse")
    assert builtin is not None
    assert courses.meta_for(builtin) is None


# --- чтение данных курса ---


def test_try_course_data_reads_valid_module(courses_home):
    write_course(courses_home)
    copy_module(courses_home, "python-basics")
    courses.refresh_courses()
    course = courses.get_course("python-basics")
    assert course is not None
    data, error = courses.try_course_data(course)
    assert error is None and data is not None
    assert len(data.topics) == 1


def test_try_course_data_reports_broken_seed(courses_home):
    """Недособранный черновик — не сбой: страница покажет текст ошибки."""
    write_course(courses_home)
    (courses_home / "python-basics" / "seed_topic01.json").write_text('{"topic": 1}', encoding="utf-8")
    courses.refresh_courses()
    course = courses.get_course("python-basics")
    assert course is not None
    data, error = courses.try_course_data(course)
    assert data is None and error


def test_invalidate_course_drops_cached_read(courses_home):
    write_course(courses_home)
    seed_path = copy_module(courses_home, "python-basics")
    courses.refresh_courses()
    course = courses.get_course("python-basics")
    assert course is not None
    assert courses.course_data(course).topics[0].number == 1

    seed_path.write_text('{"topic": 1}', encoding="utf-8")  # правка файла «извне»
    assert courses.course_data(course).topics[0].number == 1  # кэш ещё жив
    courses.invalidate_course("python-basics")
    fresh = courses.get_course("python-basics")
    assert fresh is not None
    assert courses.try_course_data(fresh)[0] is None


def test_get_course_reads_catalog_without_explicit_refresh(courses_home):
    write_course(courses_home)
    assert courses.get_course("python-basics") is not None


# --- счётчики id ---


def test_allocate_ids_is_monotonic_and_saved(courses_home):
    courses.create_course(title="Основы Python")
    assert courses.allocate_ids("osnovy-python", "item", 2) == [1, 2]
    assert courses.allocate_ids("osnovy-python", "item", 1) == [3]
    assert store.read_meta(courses_home / "osnovy-python").next_item_id == 4
    # Счётчики видов id независимы, встроенный курс их не имеет.
    assert courses.allocate_ids("osnovy-python", "rubric", 1) == [1]
    with pytest.raises(store.StoreError):
        courses.allocate_ids("mlcourse", "item", 1)


def test_allocate_ids_never_repeats_after_regeneration(courses_home):
    """Иначе перегенерация переписала бы записи журнала ученика (upsert по id)."""
    courses.create_course(title="Основы Python")
    first = courses.allocate_ids("osnovy-python", "item", 3)
    second = courses.allocate_ids("osnovy-python", "item", 3)
    assert set(first).isdisjoint(second)


def test_allocate_rejects_unknown_kind(courses_home):
    courses.create_course(title="Основы Python")
    with pytest.raises(store.StoreError):
        courses.allocate_ids("osnovy-python", "lesson", 1)


# --- находки аудита срезов A–B ---


def test_device_names_are_rejected(courses_home):
    """CON/NUL годятся под шаблон id, но папкой на Windows не станут."""
    for name in ("con", "nul", "com1", "lpt9"):
        with pytest.raises(store.StoreError):
            store.check_id(name)
    assert store.slugify("Nul") == "course"


def test_save_meta_rejects_foreign_id(courses_home):
    """Метаданные с чужим id убрали бы курс из реестра молча."""
    courses.create_course(title="Основы Python")
    meta = courses.meta_for(courses.get_course("osnovy-python"))  # type: ignore[arg-type]
    assert meta is not None
    meta.id = "someone-else"
    with pytest.raises(store.StoreError):
        courses.save_meta("osnovy-python", meta)
    assert courses.get_course("osnovy-python") is not None


def test_atomic_write_cleans_up_after_failure(tmp_path, monkeypatch):
    """Сбой записи не оставляет временных файлов рядом с курсом."""
    target = tmp_path / "course.json"
    target.write_text("прежнее", encoding="utf-8")

    def boom(src, dst):
        raise PermissionError("файл занят")

    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(PermissionError):
        store.atomic_write_text(target, "новое")
    assert [path.name for path in tmp_path.iterdir()] == ["course.json"]
    assert target.read_text(encoding="utf-8") == "прежнее"


def test_allocate_refuses_absurd_count(courses_home):
    courses.create_course(title="Основы Python")
    with pytest.raises(store.StoreError):
        courses.allocate_ids("osnovy-python", "item", 10**9)
    # Счётчик после отказа не сдвинулся.
    assert store.read_meta(courses_home / "osnovy-python").next_item_id == 1


def test_missing_course_is_not_rescanned(courses_home, monkeypatch):
    """Опечатка в query админки не должна обходить каталог на каждый запрос."""
    calls = []
    original = store.scan
    monkeypatch.setattr(store, "scan", lambda path: calls.append(path) or original(path))

    assert courses.get_course("no-such-course") is None
    assert courses.get_course("no-such-course") is None
    assert len(calls) == 1
