"""Тесты метрик согласия и прогона eval (Срез 7.2)."""

import pytest

from llm_tutor.course.seed import load_seed
from llm_tutor.eval import metrics
from llm_tutor.eval.grader_eval import (
    EvalReport,
    GoldenCase,
    GoldenCriterion,
    GoldenSet,
    format_report,
    run_eval,
)
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict


class _FixedGrader:
    """Грейдер, который всегда возвращает один и тот же вердикт."""

    def __init__(self, verdict: RubricVerdict) -> None:
        self.verdict = verdict

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        return self.verdict


# --- метрики ---


def test_agreement_counts_confusion_matrix() -> None:
    result = metrics.agreement(
        [(True, True), (True, False), (False, True), (False, False)]
    )

    assert (result.true_positive, result.false_positive) == (1, 1)
    assert (result.false_negative, result.true_negative) == (1, 1)
    assert result.total == 4


def test_accuracy_precision_and_recall() -> None:
    result = metrics.agreement([(True, True), (True, True), (False, True), (False, False)])

    assert result.accuracy == pytest.approx(0.75)
    assert result.precision == pytest.approx(1.0)
    assert result.recall == pytest.approx(2 / 3)


def test_kappa_is_one_on_perfect_agreement() -> None:
    assert metrics.agreement([(True, True), (False, False)]).kappa == pytest.approx(1.0)


def test_kappa_penalises_all_positive_predictions() -> None:
    """«Всё засчитано» при перекосе: accuracy высокая, kappa около нуля."""
    result = metrics.agreement([(True, True)] * 9 + [(True, False)])

    assert result.accuracy == pytest.approx(0.9)
    assert result.kappa < 0.1


def test_empty_agreement_is_zero_not_error() -> None:
    result = metrics.agreement([])

    assert result.total == 0
    assert (result.accuracy, result.precision, result.recall, result.kappa) == (
        0.0,
        0.0,
        0.0,
        0.0,
    )


# --- прогон по набору ---


async def test_run_eval_matches_labels(conn, settings) -> None:
    load_seed(conn)
    golden = GoldenSet(
        course="test",
        cases=[
            GoldenCase(
                id=1,
                item_id=9,
                answer="groupby разбивает строки",
                criteria=[
                    GoldenCriterion(id=1, passed=True),
                    GoldenCriterion(id=2, passed=False),
                    GoldenCriterion(id=3, passed=False),
                ],
            )
        ],
    )
    verdict = RubricVerdict(
        criteria=[
            CriterionVerdict(id=1, passed=True, quote="groupby"),
            CriterionVerdict(id=2, passed=False),
            CriterionVerdict(id=3, passed=False),
        ]
    )

    report = await run_eval(conn, _FixedGrader(verdict), "m", golden, settings=settings)

    assert report.cases == 1
    assert report.agreement.accuracy == 1.0
    assert report.agreement.total == 3
    assert report.hallucinated_quotes == 0


async def test_run_eval_counts_hallucinated_quotes(conn, settings) -> None:
    load_seed(conn)
    golden = GoldenSet(
        course="test",
        cases=[
            GoldenCase(
                id=1,
                item_id=9,
                answer="совсем другой текст",
                criteria=[GoldenCriterion(id=1, passed=True)],
            )
        ],
    )
    verdict = RubricVerdict(
        criteria=[CriterionVerdict(id=1, passed=True, quote="выдуманная цитата")]
    )

    report = await run_eval(conn, _FixedGrader(verdict), "m", golden, settings=settings)

    assert report.hallucinated_quotes == 1
    assert report.agreement.false_negative == 1  # код не засчитал критерий


async def test_run_eval_reports_missing_item(conn, settings) -> None:
    load_seed(conn)
    golden = GoldenSet(
        course="test",
        cases=[GoldenCase(id=7, item_id=999, answer="x", criteria=[])],
    )

    report = await run_eval(conn, _FixedGrader(RubricVerdict()), "m", golden, settings=settings)

    assert report.failed_cases == (7,)
    assert report.agreement.total == 0


def test_format_report_mentions_key_numbers() -> None:
    report = EvalReport(
        cases=2, agreement=metrics.agreement([(True, True)]), hallucinated_quotes=1
    )

    text = format_report(report)

    assert "Kappa" in text
    assert "Цитат" in text
    assert "2" in text
