"""Прогон грейдера по золотому набору (Срез 7.2).

Гоняет РЕАЛЬНУЮ модель (нужен ключ OpenRouter), поэтому это не тест, а
отдельная команда:

    uv run python -m llm_tutor.eval.grader_eval

Считает согласие по критериям (kappa, точность, полнота), матрицу ошибок и
отдельно — случаи, где модель сослалась на цитату, которой в ответе нет.
Это грубый фильтр, а не откалиброванная метрика: набор мал, а разметка
сделана одним человеком.
"""

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from llm_tutor.config import get_settings
from llm_tutor.course.seed import DEFAULT_SEED_PATH, load_seed
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.eval.metrics import Agreement, agreement
from llm_tutor.grader.rubric import check_quote, grade_with_verdict
from llm_tutor.llm.client import LLMClient

# Корень проекта (src/llm_tutor/eval/grader_eval.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GOLDEN_PATH = _PROJECT_ROOT / "data" / "golden_set_topic01.json"


class GoldenCriterion(BaseModel):
    """Разметка одного критерия человеком: выполнен или нет."""

    id: int
    passed: bool


class GoldenCase(BaseModel):
    """Один размеченный ответ ученика на задание из банка."""

    id: int
    item_id: int
    answer: str
    criteria: list[GoldenCriterion]


class GoldenSet(BaseModel):
    """Золотой набор: ответы с разметкой по критериям."""

    course: str
    cases: list[GoldenCase]


@dataclass(frozen=True)
class EvalReport:
    """Итог прогона: согласие по критериям и выдуманные цитаты."""

    cases: int
    agreement: Agreement
    hallucinated_quotes: int
    failed_cases: tuple[int, ...] = ()


def load_golden(path: str | Path = DEFAULT_GOLDEN_PATH) -> GoldenSet:
    """Читает и валидирует золотой набор."""
    return GoldenSet.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


async def run_eval(
    conn,
    client: LLMClient,
    model: str,
    golden: GoldenSet,
    *,
    settings=None,
) -> EvalReport:
    """Прогоняет грейдер по набору и считает согласие с разметкой."""
    pairs: list[tuple[bool, bool]] = []
    hallucinated = 0
    failed: list[int] = []

    for case in golden.cases:
        item = repos.get_item(conn, case.item_id)
        if item is None:
            failed.append(case.id)
            continue
        verdict, criteria, result = await grade_with_verdict(
            conn, client, model, item, case.answer, settings=settings
        )
        labels = {label.id: label.passed for label in case.criteria}
        for criterion, graded in zip(criteria, result.criteria):
            if criterion.id in labels:
                pairs.append((graded.passed, labels[criterion.id]))
        hallucinated += sum(
            1
            for entry in verdict.criteria
            if entry.passed and not check_quote(entry.quote, case.answer)
        )

    return EvalReport(
        cases=len(golden.cases),
        agreement=agreement(pairs),
        hallucinated_quotes=hallucinated,
        failed_cases=tuple(failed),
    )


def format_report(report: EvalReport) -> str:
    """Человекочитаемый отчёт по итогам прогона."""
    a = report.agreement
    return "\n".join(
        [
            f"Ответов в наборе: {report.cases}",
            f"Решений по критериям: {a.total}",
            f"Согласие (accuracy): {a.accuracy:.1%}",
            f"Kappa Коэна: {a.kappa:.3f}",
            f"Точность (precision): {a.precision:.1%}",
            f"Полнота (recall): {a.recall:.1%}",
            "Матрица ошибок (засчитано/размечено):",
            f"  верно засчитано: {a.true_positive}, лишнее засчитано: {a.false_positive}",
            f"  пропущено: {a.false_negative}, верно не засчитано: {a.true_negative}",
            f"Цитат, которых нет в ответе: {report.hallucinated_quotes}",
            f"Пропущенных заданий: {list(report.failed_cases)}",
        ]
    )


async def _run(args: argparse.Namespace) -> EvalReport:
    settings = get_settings()
    conn = get_conn(args.db)
    try:
        migrate(conn)
        load_seed(conn, args.seed)
        golden = load_golden(args.golden)
        client = LLMClient(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
            default_model=settings.grader_model,
            temperature=0.0,  # грейдер оценивает одинаково при повторе
        )
        try:
            return await run_eval(
                conn, client, settings.grader_model, golden, settings=settings
            )
        finally:
            await client.aclose()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI: прогнать грейдер по золотому набору и напечатать отчёт."""
    parser = argparse.ArgumentParser(description="Прогон грейдера по золотому набору.")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN_PATH))
    parser.add_argument("--seed", default=str(DEFAULT_SEED_PATH))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", ":memory:"))
    args = parser.parse_args(argv)

    report = asyncio.run(_run(args))
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
