"""Аудит среза 24: регрессии находок ревью загрузки курса."""

import json

import pytest
from course_fixtures import MINI_SEED_PATH, load_two_modules

from llm_tutor.course import seed as seed_mod
from llm_tutor.course.seed import DEFAULT_SEED_PATH, SeedError, course_paths, load_course_data
from llm_tutor.db import repos
from llm_tutor.eval import grader_eval


def _mini() -> dict:
    return json.loads(MINI_SEED_PATH.read_text(encoding="utf-8"))


def _course_with(tmp_path, data: dict, name: str = "seed_topic02_mini.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_course_data([DEFAULT_SEED_PATH, path])


# M1: загрузка одного файла не гасит остальные модули рабочей БД.


def test_grader_eval_loads_whole_course(conn, monkeypatch) -> None:
    load_two_modules(conn)
    monkeypatch.setattr(seed_mod, "course_paths", lambda: [DEFAULT_SEED_PATH, MINI_SEED_PATH])

    grader_eval.load_eval_course(conn)

    assert [topic.number for topic in repos.get_topics(conn)] == [1, 2]


def test_grader_eval_ignores_db_path(monkeypatch) -> None:
    """Прогон грейдера пишет в БД — по умолчанию только в память, не в DB_PATH."""
    monkeypatch.setenv("DB_PATH", "data/llm_tutor.sqlite3")

    assert grader_eval.build_parser().parse_args([]).db == ":memory:"


def test_seed_cli_single_file_needs_explicit_db(capsys) -> None:
    """--seed гасит остальные модули — без явного --db (рабочая БД) запрещено."""
    with pytest.raises(SystemExit):
        seed_mod.main(["--seed", str(DEFAULT_SEED_PATH)])
    assert "--db" in capsys.readouterr().err


# M2: ребро из файла модуля N ведёт только в тему модуля N.


def test_edge_into_other_module_is_rejected(tmp_path) -> None:
    data = _mini()
    data["edges"].append(
        {"from_id": "pandas_intro", "to_id": "numpy_basics", "type": "requires", "hard": True}
    )
    with pytest.raises(SeedError, match="чужого модуля"):
        _course_with(tmp_path, data)


# L1: ошибка битого файла называет файл.


def test_broken_json_names_the_file(tmp_path) -> None:
    path = tmp_path / "seed_topic02.json"
    path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(SeedError, match="seed_topic02.json"):
        load_course_data([DEFAULT_SEED_PATH, path])


def test_missing_topic_section_names_the_file(tmp_path) -> None:
    data = _mini()
    del data["topic"]
    with pytest.raises(SeedError, match="seed_topic02_mini.json"):
        _course_with(tmp_path, data)


# L2: черновики и бэкапы с суффиксом в курс не попадают.


def test_course_paths_skip_suffixed_drafts(tmp_path) -> None:
    for name in ("seed_topic01.json", "seed_topic01_backup.json", "seed_topic02_черновик.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert [path.name for path in course_paths(tmp_path)] == ["seed_topic01.json"]


# L4: пустой модуль и пустой блок анкеты — ошибка.


def test_module_without_topics_is_rejected(tmp_path) -> None:
    data = _mini()
    data["nodes"], data["edges"], data["items"] = [], [], []
    for block in data["topic"]["survey"]["blocks"]:
        block["concepts"] = []
    with pytest.raises((SeedError, ValueError)):
        _course_with(tmp_path, data)


def test_survey_block_without_topics_is_rejected(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["blocks"].append(
        {"key": "t02_empty", "title": "Пусто", "question": "?", "example": "-", "concepts": []}
    )
    with pytest.raises((SeedError, ValueError)):
        _course_with(tmp_path, data)


# M3 (решение пользователя: фильтр по модулю): задание будущего модуля не
# выдаётся в уроке темы прошлого модуля.


def _lesson_items(conn, node_id: str) -> list[int]:
    from llm_tutor.student import diagnostic

    used: frozenset[int] = frozenset()
    while (question := diagnostic.verification_item(conn, node_id, used_item_ids=used)) is not None:
        used = used | {question.item.id}
    return sorted(used)


def test_lesson_of_earlier_module_skips_later_module_items(conn) -> None:
    load_two_modules(conn)

    items = _lesson_items(conn, "describe_stats")

    assert items and 2006 not in items


def test_diagnostic_does_not_measure_earlier_topic_by_later_item(conn, settings) -> None:
    from llm_tutor.course.graph import CourseGraph
    from llm_tutor.student import diagnostic

    load_two_modules(conn)
    graph = CourseGraph.load(conn)
    asked: frozenset[int] = frozenset()
    while (q := diagnostic.next_question(conn, graph, asked_item_ids=asked, settings=settings)):
        assert not (q.item.id >= 2000 and graph.topic_of(q.concept_id) == 1), q
        asked = asked | {q.item.id}
