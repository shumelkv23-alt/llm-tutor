"""Эталон «Объясни новичку» встроенного курса: на занятие (срезы 90, 92)."""

import re
from pathlib import Path

from llm_tutor.course.seed import load_course_data
from llm_tutor.schemas import Topic

# Важные занятия: после их закрытия ученик объясняет их новичку (спека 2026-10-27).
IMPORTANT = {
    "selection", "grouping", "eda_case",
    "distributions", "numeric_relations",
    "tree_criteria", "knn", "validation",
    "linreg", "logreg", "nonlinear",
    "bagging_basics", "forest", "feature_importance",
}  # fmt: skip

CLAIMS_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_explain_claims.py"


def test_exactly_important_lessons_have_explain() -> None:
    course = load_course_data()
    assert {node.id for node in course.nodes if node.explain is not None} == IMPORTANT


def test_no_module_level_explain() -> None:
    # Эталон живёт в занятиях; модульный эталон из схемы ушёл (срез 94).
    assert "explain" not in Topic.model_fields


def test_no_explain_warnings() -> None:
    assert not any("важных занятий" in warning for warning in load_course_data().warnings)


def test_claims_script_ids_exist() -> None:
    course = load_course_data()
    pitfalls = {
        pitfall.id
        for node in course.nodes
        if node.explain is not None
        for pitfall in node.explain.pitfalls
    }
    checked = set(re.findall(r'@check\("([^"]+)"\)', CLAIMS_SCRIPT.read_text(encoding="utf-8")))
    assert checked, "в скрипте нет проверок"
    assert checked <= pitfalls, sorted(checked - pitfalls)


def test_beliefs_are_questions() -> None:
    # Загвоздка звучит от лица новичка — догадкой-вопросом, а не утверждением.
    for node in load_course_data().nodes:
        for pitfall in node.explain.pitfalls if node.explain else []:
            assert pitfall.belief.endswith("?"), pitfall.id


def test_every_library_pitfall_is_checked_by_running() -> None:
    """Загвоздки про поведение библиотек проверены скриптом (CLAUDE.md: «проверяй запуском»)."""
    checked = set(re.findall(r'@check\("([^"]+)"\)', CLAIMS_SCRIPT.read_text(encoding="utf-8")))
    must_check = {
        "iloc_by_label", "loc_slice_exclusive", "groupby_returns_table", "box_line_is_mean",
        "leaf_not_pure", "knn_scale_irrelevant", "knn_k1_best", "weight_is_importance",
        "logreg_predicts_number", "bigger_c_stronger_reg", "identical_copies",
        "bootstrap_is_shuffle", "many_trees_overfit", "oob_not_train", "correlated_share",
    }  # fmt: skip
    assert must_check <= checked, sorted(must_check - checked)
