"""Разделы в данных (срез 53): схемы, миграция 006, правила занятий в seed."""

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from course_fixtures import LESSON_SEED_PATHS, LESSONS_DIR, load_lessons
from pydantic import ValidationError

from llm_tutor.course import seed as seed_module
from llm_tutor.course.seed import (
    MAX_LESSONS,
    SeedError,
    lessons_without_final,
    load_course_data,
    main_concept,
    theory_headings,
    thin_sections,
)
from llm_tutor.db import connection, repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.schemas import Item, SessionState


def _course_copy(tmp_path: Path, mutate: Callable[[dict, dict], None] | None = None) -> list[Path]:
    """Копия фикстуры в tmp_path; ``mutate(m1, m2)`` правит seed модулей."""
    target = tmp_path / "lessons"
    shutil.copytree(LESSONS_DIR, target)
    paths = [target / path.name for path in LESSON_SEED_PATHS]
    seeds = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if mutate is not None:
        mutate(*seeds)
    for path, data in zip(paths, seeds):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return paths


def _load(tmp_path: Path, mutate: Callable[[dict, dict], None]):
    return load_course_data(_course_copy(tmp_path, mutate))


def _node(seed: dict, node_id: str) -> dict:
    return next(node for node in seed["nodes"] if node["id"] == node_id)


def _section_items(seed: dict, node_id: str, section: str) -> list[dict]:
    return [
        item
        for item in seed["items"]
        if item.get("section") == section and node_id in item["concept_weights"]
    ]


# --- фикстура и БД ---


def test_fixture_course_loads_with_sections_and_legacy() -> None:
    course = load_course_data(LESSON_SEED_PATHS)
    assert [topic.number for topic in course.topics] == [1, 2]
    assert all(topic.published for topic in course.topics)
    assert len(course.nodes) == 6
    assert all(len(node.sections) == 2 for node in course.nodes)
    assert sum(item.final for item in course.items) == 6
    assert course.legacy["old_groupby"].node == "l1_group"
    assert course.legacy["old_groupby"].section == "groupby"
    assert course.legacy["old_python"] is None
    assert "old_hist" in course.legacy  # таблицы модулей склеиваются
    assert thin_sections(course) == []
    assert lessons_without_final(course) == []


def test_sections_final_and_published_round_trip_through_db(conn) -> None:
    load_lessons(conn)
    concepts = {concept.id: concept for concept in repos.get_concepts(conn)}
    assert [s.id for s in concepts["l1_group"].sections] == ["groupby", "pivot"]
    assert concepts["l1_group"].sections[1].title == "pivot_table"
    items = {item.id: item for item in repos.get_items(conn)}
    assert items[3001].section == "series" and not items[3001].final
    final = next(item for item in items.values() if item.final)
    assert final.section is None
    assert repos.get_item(conn, final.id).final
    assert repos.get_topic(conn, 2).published

    # Снятая публикация и разделы обновляются повторной загрузкой (истина — seed).
    course = load_course_data(LESSON_SEED_PATHS)
    topics = [topic.model_copy(update={"published": False}) for topic in course.topics]
    repos.replace_seed(conn, course.nodes, course.edges, course.items, topics=topics)
    assert not repos.get_topic(conn, 1).published


def test_flat_concepts_and_items_keep_defaults(conn) -> None:
    """Узел и задание старого формата: разделов нет, не итоговое, модуль открыт."""
    seed_module.load_course(conn)
    concept = next(c for c in repos.get_concepts(conn) if c.id == "groupby")
    assert concept.sections == []
    assert all(item.section is None and not item.final for item in repos.get_items(conn))
    assert all(topic.published for topic in repos.get_topics(conn))


def test_migration_006_adds_columns_with_defaults(tmp_path) -> None:
    """БД на версии 5 с курсом получает разделы, раздел/итоговое и публикацию."""
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    for script in sorted(connection.MIGRATIONS_DIR.glob("00[1-5]_*.sql")):
        old.executescript(script.read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 5")
    old.execute("INSERT INTO concepts (id, name) VALUES ('groupby', 'groupby')")
    old.execute(
        "INSERT INTO items (id, concept_weights, answer_type, prompt) "
        "VALUES (18, '{\"groupby\": 1.0}', 'open', 'Вопрос')"
    )
    old.execute("INSERT INTO topics (id, title, survey) VALUES (1, 'Модуль', '{}')")
    old.commit()
    old.close()

    upgraded = get_conn(path)
    try:
        migrate(upgraded)
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 6
        row = upgraded.execute("SELECT sections FROM concepts").fetchone()
        assert row["sections"] == "[]"
        row = upgraded.execute("SELECT section, final FROM items").fetchone()
        assert row["section"] is None and row["final"] == 0
        assert upgraded.execute("SELECT published FROM topics").fetchone()["published"] == 1
        assert repos.get_item(upgraded, 18).final is False
    finally:
        upgraded.close()


def test_session_state_section_fields_default_and_old_json() -> None:
    """Старое состояние без полей разделов читается с дефолтами."""
    state = SessionState.model_validate_json('{"current_node_id": "groupby"}')
    assert state.section_id is None
    assert state.passed_sections == [] and state.weak_sections == []
    assert state.final_passed is False


# --- схема задания ---


def test_final_item_cannot_have_section() -> None:
    with pytest.raises(ValidationError, match="итогового"):
        Item(id=1, prompt="?", answer_type="short", answer="x", section="a", final=True)


def test_main_concept_is_heaviest_then_first() -> None:
    item = Item(id=1, prompt="?", concept_weights={"a": 0.5, "b": 1.0, "c": 1.0})
    assert main_concept(item) == "b"
    assert main_concept(Item(id=2, prompt="?")) is None


# --- число занятий и разделов ---


def _add_lessons(seed: dict, count: int) -> None:
    """Добавляет занятия-копии последнего занятия модуля (с заданиями и конспектом)."""
    template = seed["nodes"][-1]
    survey_block = seed["topic"]["survey"]["blocks"][-1]
    next_id = max(item["id"] for item in seed["items"]) + 1
    for k in range(count):
        node_id = f"{template['id']}_x{k}"
        seed["nodes"].append({**template, "id": node_id})
        survey_block["concepts"].append(node_id)
        for item in [i for i in seed["items"] if template["id"] in i["concept_weights"]]:
            seed["items"].append({**item, "id": next_id, "concept_weights": {node_id: 1.0}})
            next_id += 1


def _copy_theory(paths: list[Path], node_from: str, count: int) -> None:
    folder = paths[0].parent / "theory" / "topic01"
    for k in range(count):
        shutil.copy(folder / f"{node_from}.md", folder / f"{node_from}_x{k}.md")


def test_module_with_max_lessons_loads_and_one_more_does_not(tmp_path) -> None:
    extra = MAX_LESSONS - 3
    paths = _course_copy(tmp_path / "ok", lambda m1, m2: _add_lessons(m1, extra))
    _copy_theory(paths, "l1_group", extra)
    assert len([n for n in load_course_data(paths).nodes if n.topic_id == 1]) == MAX_LESSONS

    paths = _course_copy(tmp_path / "over", lambda m1, m2: _add_lessons(m1, extra + 1))
    _copy_theory(paths, "l1_group", extra + 1)
    with pytest.raises(SeedError, match=f"11 занятий, а можно не больше {MAX_LESSONS}"):
        load_course_data(paths)


def test_flat_module_is_not_limited_by_max_lessons() -> None:
    """Старый формат (21 тема в модуле) грузится: правила занятий его не касаются."""
    course = load_course_data()
    assert sum(node.topic_id == 1 for node in course.nodes) > MAX_LESSONS


def test_module_mixing_lessons_and_flat_nodes_fails(tmp_path) -> None:
    def mutate(m1, m2):
        _node(m1, "l1_select")["sections"] = []
        m1["items"] = [i for i in m1["items"] if "l1_select" not in i["concept_weights"]]

    with pytest.raises(SeedError, match=r"не у всех занятий: нет у \['l1_select'\]"):
        _load(tmp_path, mutate)


@pytest.mark.parametrize("count", [1, 5])
def test_lesson_needs_two_to_four_sections(tmp_path, count: int) -> None:
    def mutate(m1, m2):
        node = _node(m1, "l1_frames")
        node["sections"] = [{"id": f"s{k}", "title": f"S{k}"} for k in range(count)]

    with pytest.raises(SeedError, match=f"{count} разделов, а нужно от 2 до 4"):
        _load(tmp_path, mutate)


def test_duplicate_section_ids_fail(tmp_path) -> None:
    def mutate(m1, m2):
        _node(m1, "l1_frames")["sections"][1]["id"] = "series"

    with pytest.raises(SeedError, match="Дубли id разделов занятия l1_frames"):
        _load(tmp_path, mutate)


# --- раздел задания ---


def test_item_section_must_belong_to_main_concept(tmp_path) -> None:
    def mutate(m1, m2):
        item = _section_items(m1, "l1_select", "loc")[0]
        # Главный концепт — l1_select (вес больше), а раздел взят из l1_frames.
        item["concept_weights"] = {"l1_frames": 0.5, "l1_select": 1.0}
        item["section"] = "series"

    with pytest.raises(SeedError, match="раздела 'series' нет в занятии l1_select"):
        _load(tmp_path, mutate)


def test_item_section_of_main_concept_with_minor_weight_elsewhere_loads(tmp_path) -> None:
    def mutate(m1, m2):
        item = _section_items(m1, "l1_select", "loc")[0]
        item["concept_weights"] = {"l1_frames": 0.5, "l1_select": 1.0}

    assert _load(tmp_path, mutate)


def test_lesson_item_without_section_fails(tmp_path) -> None:
    def mutate(m1, m2):
        del _section_items(m1, "l1_frames", "series")[0]["section"]

    with pytest.raises(SeedError, match="без раздела и не итоговое"):
        _load(tmp_path, mutate)


@pytest.mark.parametrize("field", [("section", "series"), ("final", True)])
def test_section_or_final_on_flat_node_item_fails(field) -> None:
    """Раздел или «итоговое» у задания темы старого формата — ошибка seed."""
    course = load_course_data()
    key, value = field
    broken = [
        item.model_copy(update={key: value}) if item.id == course.items[0].id else item
        for item in course.items
    ]
    seeds = [seed_module._parse_seed(path) for path in seed_module.course_paths()]
    seeds[0].items = [item for item in broken if item.id in {i.id for i in seeds[0].items}]
    with pytest.raises(SeedError, match="только у заданий занятия с разделами"):
        seed_module.build_course(seeds)


# --- содержание опубликованного модуля ---


def _thin_section(m1: dict, m2: dict) -> None:
    m1["items"].remove(_section_items(m1, "l1_group", "pivot")[0])


def test_published_section_needs_two_items(tmp_path) -> None:
    with pytest.raises(SeedError, match="l1_group/pivot опубликованного модуля 1 меньше 2"):
        _load(tmp_path, _thin_section)


def _no_final(m1: dict, m2: dict) -> None:
    m2["items"] = [
        i for i in m2["items"] if not (i.get("final") and "l2_box" in i["concept_weights"])
    ]


def test_published_lesson_needs_final_item(tmp_path) -> None:
    with pytest.raises(SeedError, match="У занятия l2_box нет итогового задания"):
        _load(tmp_path, _no_final)


def test_unpublished_module_loads_without_content_and_cli_lists_gaps(tmp_path, capsys) -> None:
    def mutate(m1, m2):
        _thin_section(m1, m2)
        _no_final(m1, m2)
        m1["topic"]["published"] = False
        m2["topic"]["published"] = False

    paths = _course_copy(tmp_path, mutate)
    (paths[0].parent / "theory" / "topic01" / "l1_frames.md").unlink()
    course = load_course_data(paths)
    assert thin_sections(course) == ["l1_group/pivot"]
    assert lessons_without_final(course) == ["l2_box"]

    # CLI грузит только файлы из data/ или один --seed: проверяем модуль 1.
    assert seed_module.main(["--seed", str(paths[0])]) == 0
    out = capsys.readouterr().out
    assert "меньше 2 заданий: l1_group/pivot" in out
    assert "Без конспекта" in out and "l1_frames" in out


def test_published_lesson_needs_theory(tmp_path) -> None:
    paths = _course_copy(tmp_path)
    (paths[0].parent / "theory" / "topic01" / "l1_frames.md").unlink()
    with pytest.raises(SeedError, match="У занятия l1_frames нет конспекта"):
        load_course_data(paths)


def _rewrite_theory(paths: list[Path], node_id: str, edit: Callable[[str], str]) -> None:
    path = paths[0].parent / "theory" / "topic01" / f"{node_id}.md"
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")


def test_theory_needs_heading_for_every_section(tmp_path) -> None:
    paths = _course_copy(tmp_path)
    _rewrite_theory(paths, "l1_select", lambda text: text.replace("## Булевы маски", "## Маски"))
    with pytest.raises(SeedError, match="нет заголовка «## Булевы маски»"):
        load_course_data(paths)


def test_theory_headings_must_follow_seed_order(tmp_path) -> None:
    def swap(text: str) -> str:
        return text.replace("## loc и iloc", "## TMP").replace(
            "## Булевы маски", "## loc и iloc"
        ).replace("## TMP", "## Булевы маски")

    paths = _course_copy(tmp_path)
    _rewrite_theory(paths, "l1_select", swap)
    with pytest.raises(SeedError, match="не по порядку разделов"):
        load_course_data(paths)


def test_heading_inside_code_block_does_not_count(tmp_path) -> None:
    def hide(text: str) -> str:
        return text.replace("## Булевы маски", "```\n## Булевы маски\n```")

    paths = _course_copy(tmp_path)
    _rewrite_theory(paths, "l1_select", hide)
    with pytest.raises(SeedError, match="нет заголовка «## Булевы маски»"):
        load_course_data(paths)


def test_theory_headings_skip_fenced_code() -> None:
    text = "# T\n## A\n```python\n## не заголовок\n```\n~~~\n## и это\n~~~\n## B\n### C\n"
    assert theory_headings(text) == ["A", "B"]


# --- legacy ---


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"l1_frames": {"node": "l1_group", "section": "groupby"}}, "живой узел курса"),
        ({"old_x": {"node": "nope", "section": "groupby"}}, "неизвестное занятие nope"),
        ({"old_x": {"node": "l1_group", "section": "nope"}}, "неизвестный раздел l1_group/nope"),
        ({"old_hist": {"node": "l1_group", "section": "groupby"}}, "нескольких модулей"),
    ],
)
def test_legacy_rules(tmp_path, entry: dict, message: str) -> None:
    def mutate(m1, m2):
        m1["legacy"].update(entry)

    with pytest.raises(SeedError, match=message):
        _load(tmp_path, mutate)


def test_legacy_entry_lives_in_file_of_target_module(tmp_path) -> None:
    """Тема модуля 1 переезжает в модуль 2: запись — в файле модуля 2 (аудит 53, L3)."""

    def in_target(m1, m2):
        m2["legacy"]["visualization_basics"] = {"node": "l2_hist", "section": "hist"}

    assert _load(tmp_path / "ok", in_target).legacy["visualization_basics"].node == "l2_hist"

    def in_source(m1, m2):
        m1["legacy"]["visualization_basics"] = {"node": "l2_hist", "section": "hist"}

    with pytest.raises(SeedError, match="запись — в файле этого модуля"):
        _load(tmp_path / "bad", in_source)


def test_seed_with_legacy_null_round_trips(conn: sqlite3.Connection) -> None:
    """Таблица legacy не пишется в БД: курс в БД тот же, что без неё."""
    course = load_lessons(conn)
    assert course.legacy["old_python"] is None
    assert {c.id for c in repos.get_concepts(conn)} == {n.id for n in course.nodes}
