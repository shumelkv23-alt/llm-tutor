"""Проект по курсу: проверки карточки и заметок, чистка ответа модели (срез 98)."""

import pytest

from llm_tutor.student.project import (
    TEMPLATES,
    card_missing,
    clean_fields,
    clean_proposal,
    next_stage,
    notes_missing,
    reply_ok,
    stage_of,
)

ML = TEMPLATES["ml"]
FULL_CARD = {
    "title": "Отток клиентов",
    "problem": "Предсказать уход клиента за месяц",
    "data": "Telco Customer Churn, 7 тысяч клиентов",
    "success": "ROC AUC выше логистической регрессии",
}


def test_card_missing() -> None:
    assert card_missing(ML, {}) == ["title", "problem", "data", "success"]
    short = {**FULL_CARD, "title": "ab", "problem": "коротко"}
    assert card_missing(ML, short) == ["title", "problem"]
    assert card_missing(ML, {**FULL_CARD, "data": "   пробелы   "}) == ["data"]
    assert card_missing(ML, FULL_CARD) == []


def test_clean_proposal() -> None:
    proposal = {"title": "  Отток  ", "evil": "x", "data": "", "problem": "п" * 2000, "success": 5}
    assert clean_proposal(ML, proposal) == {"title": "Отток", "problem": "п" * 1000}
    assert clean_proposal(ML, None) == {}
    assert clean_proposal(ML, {"title": "<b>x</b>" * 20}) == {"title": ("<b>x</b>" * 20)[:80]}


def test_clean_fields_rejects_unknown_and_long() -> None:
    fields = stage_of(ML, "data").fields
    assert clean_fields(fields, {"done": "  загрузил  ", "code": ""}) == {
        "done": "загрузил",
        "code": "",
    }
    with pytest.raises(ValueError):
        clean_fields(fields, {"nope": "x"})
    with pytest.raises(ValueError):
        clean_fields(fields, {"code": "x" * 8001})
    with pytest.raises(ValueError):
        clean_fields(fields, {"done": 5})
    assert clean_fields(fields, {"code": "x" * 8000}) == {"code": "x" * 8000}


def _block(lines: int) -> str:
    return "```python\n" + "\n".join(f"x{i} = {i}" for i in range(lines)) + "\n```"


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Попробуй сначала посмотреть на пропуски.", True),
        (f"Вот так:\n{_block(9)}", False),
        (f"Сначала\n{_block(5)}\nпотом\n{_block(5)}", True),
        ("Начни так:\n```python\n" + "\n".join(f"y{i} = 1" for i in range(9)), False),
        ("   ", False),
        ("", False),
        ("а" * 3001, False),
        ("а" * 3000, True),
        (f"Ровно предел:\n{_block(8)}", True),
        # Аудит среза 101: обходы проверки (markdown рисует кодом не только ```).
        ("Вот:\n~~~python\n" + "\n".join(f"a{i} = {i}" for i in range(9)) + "\n~~~", False),
        ("Вот:\n\n" + "\n".join(f"    b{i} = {i}" for i in range(20)), False),
        ("\n\n".join(_block(8) for _ in range(5)), False),
        ("\n\n".join(_block(6) for _ in range(3)), False),
        ("\n\n".join(_block(8) for _ in range(2)), True),
        ("Вызови ```df.head()``` и посмотри, потом ```df.info()``` тоже.", True),
    ],
)
def test_reply_ok(text: str, ok: bool) -> None:
    assert reply_ok(text, max_code_lines=8, max_chars=3000) is ok


def test_notes_missing_and_next_stage() -> None:
    baseline = stage_of(ML, "baseline")
    assert notes_missing(baseline, {"model": "Логистическая регрессия"}) == ["metric"]
    assert notes_missing(baseline, {"model": "короткo", "metric": "ROC AUC 0.84 на тесте"}) == [
        "model"
    ]
    assert notes_missing(baseline, {"model": "Логистическая регрессия", "metric": "ROC AUC 0.84"}) == []
    assert next_stage(ML, "data") == "baseline"
    assert next_stage(ML, "improve") == "conclusions"
    assert next_stage(ML, "conclusions") is None
    with pytest.raises(KeyError):
        stage_of(ML, "nope")


# --- итог этапа от наставника (срез 102) ---


def test_stage_proposal_fields() -> None:
    proposal = {"model": " Дерево ", "metric": "ROC AUC", "code": "x=1", "title": "T", "junk": "y"}
    assert clean_proposal(ML, proposal, stage=stage_of(ML, "baseline")) == {
        "model": "Дерево",
        "metric": "ROC AUC",
    }


def test_number_guard() -> None:
    """Число, которого ученик не называл, выбрасывает поле итога целиком."""
    proposal = {"model": "Дерево глубины 5", "metric": "ROC AUC 0.91"}
    cleaned = clean_proposal(
        ML, proposal, stage=stage_of(ML, "baseline"), student_text="дерево depth=5, auc 0,79"
    )
    assert cleaned == {"model": "Дерево глубины 5"}


def test_number_guard_decimal_comma() -> None:
    proposal = {"metric": "ROC AUC 0,79 на тесте"}
    cleaned = clean_proposal(ML, proposal, stage=stage_of(ML, "baseline"), student_text="auc 0.79")
    assert cleaned == {"metric": "ROC AUC 0,79 на тесте"}


def test_number_guard_off() -> None:
    proposal = {"model": "Дерево глубины 5", "metric": "ROC AUC 0.91"}
    assert clean_proposal(ML, proposal, stage=stage_of(ML, "baseline")) == proposal


def test_card_proposal_ignores_stage_fields() -> None:
    assert clean_proposal(ML, {"title": "Отток", "model": "Дерево"}) == {"title": "Отток"}
