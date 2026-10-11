"""Регрессии по ревью среза 53: правила занятий в seed."""

import json
import logging

import pytest
from course_fixtures import load_lessons
from pydantic import ValidationError
from test_lessons_seed import _course_copy, _load, _node, _section_items

from llm_tutor.course import seed as seed_module
from llm_tutor.course.seed import SeedError, theory_headings
from legacy_course import load_course_data
from llm_tutor.db import repos
from llm_tutor.schemas import Section


# --- M1: погашенные задания не считаются ---


def test_inactive_items_do_not_count_for_published_section(tmp_path) -> None:
    def mutate(m1, m2):
        for item in _section_items(m1, "l1_group", "pivot"):
            item["active"] = False

    with pytest.raises(SeedError, match="l1_group/pivot опубликованного модуля 1 меньше 2"):
        _load(tmp_path, mutate)


def test_inactive_final_does_not_count(tmp_path) -> None:
    def mutate(m1, m2):
        for item in m1["items"]:
            if item.get("final") and "l1_group" in item["concept_weights"]:
                item["active"] = False

    with pytest.raises(SeedError, match="У занятия l1_group нет итогового задания"):
        _load(tmp_path, mutate)


# --- L1: заголовки — по CommonMark, как в рендере ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("~~~\n```\n## Fake\n```\n~~~\n## B\n", ["B"]),
        ("````md\n```\n## Fake\n```\n````\n## B\n", ["B"]),
        ("```\n~~~\n## Fake\n```\n## B\n", ["B"]),
        ("    ```\n## A\n## B\n", ["A", "B"]),
        ("##\tA\n## B\n", ["A", "B"]),
        ("  ## A\n## B\n", ["A", "B"]),
        ("## A ##\n## B\n", ["A", "B"]),
        ("A\n---\n\n## B\n", ["A", "B"]),
        ("## A\r\n\r\n## B\r\n", ["A", "B"]),
        ("### A\n## B\n", ["B"]),
    ],
)
def test_theory_headings_follow_commonmark(text: str, expected: list[str]) -> None:
    assert theory_headings(text) == expected


def test_theory_with_bom_keeps_first_heading(tmp_path) -> None:
    paths = _course_copy(tmp_path)
    path = paths[0].parent / "theory" / "topic01" / "l1_frames.md"
    # Первый раздел — первой строкой файла, а файл сохранён с BOM (Блокнот).
    path.write_text("﻿## Series\n\nТекст.\n\n## DataFrame\n\nТекст.\n", encoding="utf-8")

    assert load_course_data(paths).theory["l1_frames"].startswith("## Series")


# --- L2: пробелы и дубли названий разделов ---


def test_section_strips_and_rejects_blank() -> None:
    assert Section(id=" a ", title="Series ").title == "Series"
    with pytest.raises(ValidationError):
        Section(id=" ", title="x")
    with pytest.raises(ValidationError):
        Section(id="a", title="   ")


def test_trailing_space_in_title_still_matches_heading(tmp_path) -> None:
    def mutate(m1, m2):
        _node(m1, "l1_frames")["sections"][0]["title"] = "Series "

    assert _load(tmp_path, mutate)


def test_duplicate_section_titles_fail(tmp_path) -> None:
    def mutate(m1, m2):
        _node(m1, "l1_frames")["sections"][1]["title"] = "Series"

    with pytest.raises(SeedError, match="Дубли id \\(названия\\) разделов занятия l1_frames"):
        _load(tmp_path, mutate)


# --- L3: один файл модуля грузится сам по себе ---


def test_single_module_file_with_legacy_loads_via_cli(tmp_path, capsys) -> None:
    def mutate(m1, m2):
        m2["legacy"]["visualization_basics"] = {"node": "l2_hist", "section": "hist"}

    paths = _course_copy(tmp_path, mutate)
    # Модуль 1 переносит свою тему в модуль 2, а файл модуля 1 грузится сам.
    assert seed_module.main(["--seed", str(paths[0])]) == 0
    assert load_course_data(paths).legacy["visualization_basics"].node == "l2_hist"
    capsys.readouterr()


# --- L4: битые разделы узла не роняют курс ---


def test_corrupt_sections_skip_only_that_node(conn, caplog) -> None:
    load_lessons(conn)
    conn.execute("UPDATE concepts SET sections = '{oops' WHERE id = 'l1_select'")
    conn.execute(
        "UPDATE concepts SET sections = ? WHERE id = 'l1_group'",
        (json.dumps([{"id": "", "title": "x"}]),),
    )
    conn.commit()

    with caplog.at_level(logging.WARNING, logger="llm_tutor.db.repos"):
        ids = {concept.id for concept in repos.get_concepts(conn)}

    assert "l1_select" not in ids and "l1_group" not in ids
    assert "l1_frames" in ids
    assert "l1_select повреждён" in caplog.text


# --- непокрытое в срезе: смешение форматов по модулям ---


def test_flat_module_next_to_lesson_module_loads(tmp_path) -> None:
    """Ruling среза 53: формат — свойство модуля; плоский модуль 1 + занятия в модуле 2."""

    def mutate(m1, m2):
        for node in m1["nodes"]:
            node["sections"] = []
        for item in m1["items"]:
            item.pop("section", None)
            item.pop("final", None)
        m1["legacy"] = {}

    course = _load(tmp_path, mutate)
    assert {n.id for n in course.nodes if n.sections} == {"l2_hist", "l2_box", "l2_corr"}


def test_legacy_to_node_without_sections_fails(tmp_path) -> None:
    def mutate(m1, m2):
        for node in m1["nodes"]:
            node["sections"] = []
        for item in m1["items"]:
            item.pop("section", None)
            item.pop("final", None)
        m1["legacy"] = {"old_x": {"node": "l1_frames", "section": "series"}}

    with pytest.raises(SeedError, match="неизвестный раздел l1_frames/series"):
        _load(tmp_path, mutate)
