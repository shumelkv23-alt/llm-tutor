"""Курс из модулей: загрузка всех seed-файлов одним проходом (срез 24)."""

import json

import pytest
from course_fixtures import MINI_SEED_PATH, TOPIC01_ORDER, load_two_modules

from llm_tutor.course.graph import CourseGraph, CourseGraphError
from llm_tutor.course.seed import (
    DEFAULT_SEED_PATH,
    SeedError,
    course_paths,
    load_course_data,
    load_seed,
    load_seed_data,
    main,
)
from llm_tutor.db import repos


def _mini() -> dict:
    return json.loads(MINI_SEED_PATH.read_text(encoding="utf-8"))


def _course_with(tmp_path, data: dict, name: str = "seed_topic02_mini.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_course_data([DEFAULT_SEED_PATH, path])


def test_two_modules_load_without_deactivating_first(conn) -> None:
    course = load_two_modules(conn)
    graph = CourseGraph.load(conn)

    assert [topic.number for topic in repos.get_topics(conn)] == [1, 2]
    assert graph.topic_of("groupby") == 1 and graph.topic_of("mini_plots") == 2
    assert len(graph.node_ids) == len(course.nodes)
    assert graph.topo_order()[: len(TOPIC01_ORDER)] == TOPIC01_ORDER


def test_single_module_load_replaces_whole_course(conn) -> None:
    """load_seed — один модуль как весь курс: остальное гаснет."""
    load_two_modules(conn)

    load_seed(conn)

    assert [topic.number for topic in repos.get_topics(conn)] == [1]
    assert not CourseGraph.load(conn).has_node("mini_plots")


def test_module_file_alone_is_not_a_course() -> None:
    """Модуль 2 ссылается на темы модуля 1 — отдельно он не валиден."""
    with pytest.raises((SeedError, CourseGraphError)):
        load_course_data([MINI_SEED_PATH])


def test_forward_edge_between_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["edges"].append(
        {"from_id": "mini_corr", "to_id": "groupby", "type": "requires", "hard": False, "weight": 0.5}
    )
    with pytest.raises(CourseGraphError, match="вперёд"):
        _course_with(tmp_path, data)


def test_duplicate_item_id_across_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["items"][0]["id"] = 1
    with pytest.raises(SeedError, match="Дубли id заданий"):
        _course_with(tmp_path, data)


def test_duplicate_node_across_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["nodes"].append({"id": "groupby", "name": "дубль"})
    with pytest.raises(SeedError, match="Дубли id узлов"):
        _course_with(tmp_path, data)


def test_duplicate_survey_key_across_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["level_key"] = "survey_level"
    with pytest.raises(SeedError, match="ключей анкеты"):
        _course_with(tmp_path, data)


def test_survey_must_cover_every_module_node(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["blocks"][1]["concepts"] = ["mini_box"]
    with pytest.raises(SeedError, match="не накрывает"):
        _course_with(tmp_path, data)


def test_survey_cannot_claim_foreign_nodes(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["blocks"][1]["concepts"].append("groupby")
    with pytest.raises(SeedError, match="чужие темы"):
        _course_with(tmp_path, data)


def test_node_in_two_blocks_is_rejected(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["blocks"][1]["concepts"].append("mini_plots")
    with pytest.raises(SeedError, match="нескольких блоках"):
        _course_with(tmp_path, data)


def test_file_name_must_match_module_number(tmp_path) -> None:
    with pytest.raises(SeedError, match="в имени"):
        _course_with(tmp_path, _mini(), name="seed_topic03.json")


def test_same_module_number_twice_is_rejected(tmp_path) -> None:
    data = _mini()
    data["topic"]["number"] = 1
    with pytest.raises(SeedError, match="модулей"):
        _course_with(tmp_path, data, name="seed_topic01_twin.json")


def test_hard_cross_module_edge_is_a_warning(tmp_path) -> None:
    data = _mini()
    data["edges"][0]["hard"] = True

    course = _course_with(tmp_path, data)

    assert any("pandas_dataframe -> mini_plots" in warning for warning in course.warnings)


def test_course_paths_take_only_module_files(tmp_path) -> None:
    for name in ("seed_topic02.json", "seed_topic01.json", "golden_set_topic01.json", "seed.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert [path.name for path in course_paths(tmp_path)] == [
        "seed_topic01.json",
        "seed_topic02.json",
    ]


def test_empty_data_dir_reports_clearly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("llm_tutor.course.seed.DATA_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="Нет seed-файлов"):
        load_course_data()


def test_seed_cli_loads_whole_course(tmp_path, capsys) -> None:
    assert main(["--db", str(tmp_path / "t.db")]) == 0
    assert "модул" in capsys.readouterr().out


def test_topic01_survey_keys_never_change() -> None:
    """На этих ключах лежат факты в рабочей БД — менять их нельзя."""
    assert load_seed_data(DEFAULT_SEED_PATH).topic.survey.keys == (
        "survey_level",
        "block_python",
        "block_tables",
        "block_loading",
        "block_selection",
        "block_analysis",
    )


# --- отложенное срезов 24 (problems.md, 24-L3, 24-L5, 24-L6) ---


def test_misnamed_module_file_is_reported_not_skipped(tmp_path) -> None:
    """24-L3: seed_topic2.json — не тихий пропуск, а внятная ошибка."""
    for name in ("seed_topic01.json", "seed_topic2.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    with pytest.raises(SeedError, match="seed_topic2.json"):
        course_paths(tmp_path)


def test_build_course_stamps_topic_of_each_node() -> None:
    """24-L5: публичный build_course сам проставляет модуль темам по их файлу."""
    from llm_tutor.course.seed import Seed, build_course

    data = _mini()
    for node in data["nodes"]:
        node["topic_id"] = 7  # чужой номер в поле темы — файл главнее
    seeds = [load_seed_data(DEFAULT_SEED_PATH), Seed.model_validate(data)]

    course = build_course(seeds)

    assert {node.topic_id for node in course.nodes if node.id.startswith("mini_")} == {2}


def test_forward_edge_of_any_type_is_rejected(tmp_path) -> None:
    """24-L6: ребро из будущего модуля назад запрещено и для не-requires типов."""
    topic01 = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    topic01["edges"].append(
        {"from_id": "mini_plots", "to_id": "pandas_dataframe", "type": "leads_to", "hard": False}
    )
    first = tmp_path / "seed_topic01.json"
    first.write_text(json.dumps(topic01, ensure_ascii=False), encoding="utf-8")

    with pytest.raises((SeedError, CourseGraphError), match="вперёд"):
        load_course_data([first, MINI_SEED_PATH])
