"""Рубричная проверка открытых и код-ответов (Срез 6).

Два рубежа. Первый — изолированный вызов модели: только задание, критерии с
калибровочными примерами и ответ, без истории чата и без уровня ученика (§9.2
архитектуры). Второй — проверка ЦИТАТ КОДОМ: критерий засчитывается, только
если дословная цитата действительно есть в ответе. Итоговый балл считает код
по весам критериев — модель балл не выставляет.
"""

import sqlite3
from collections.abc import Sequence

from pydantic import BaseModel, Field

from llm_tutor.config import Settings, get_settings
from llm_tutor.db import repos
from llm_tutor.grader.autocheck import normalize_answer
from llm_tutor.llm.client import LLMClient
from llm_tutor.llm.prompts import GRADER_SYSTEM_PROMPT, format_grader_request
from llm_tutor.llm.schemas import ChatMessage
from llm_tutor.schemas import Criterion, CriterionResult, GradeResult, Item


class RubricError(ValueError):
    """Задание не подходит для рубричной проверки."""


class CriterionVerdict(BaseModel):
    """Вердикт модели по одному критерию (цитату ещё проверит код)."""

    id: int
    passed: bool
    quote: str | None = None


class RubricVerdict(BaseModel):
    """Структурированный ответ грейдера."""

    criteria: list[CriterionVerdict] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def check_quote(quote: str | None, answer: str) -> bool:
    """Есть ли дословная цитата в ответе (регистр и пробелы не в счёт)."""
    if not quote:
        return False
    return normalize_answer(quote) in normalize_answer(answer)


def verdict_to_result(
    criteria: Sequence[Criterion], verdict: RubricVerdict, answer: str
) -> GradeResult:
    """Считает итог кодом: не подтверждённая цитатой критерия не засчитывается.

    Критерий, которого нет в вердикте модели, тоже считается невыполненным —
    «промолчала» не значит «засчитала».
    """
    by_id = {entry.id: entry for entry in verdict.criteria}
    results: list[CriterionResult] = []
    total = 0.0
    earned = 0.0
    for criterion in criteria:
        total += criterion.weight
        entry = by_id.get(criterion.id)
        passed = bool(entry and entry.passed and check_quote(entry.quote, answer))
        if passed:
            earned += criterion.weight
        results.append(
            CriterionResult(
                criterion=criterion.criterion,
                passed=passed,
                quote=entry.quote if entry and passed else None,
            )
        )
    return GradeResult(
        criteria=results,
        score=(earned / total) if total > 0.0 else 0.0,
        confidence=verdict.confidence,
    )


async def grade(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    item: Item,
    answer: str,
    *,
    settings: Settings | None = None,
) -> GradeResult:
    """Оценивает открытый или код-ответ по рубрике задания."""
    if item.rubric_id is None:
        raise RubricError(f"У задания {item.id} нет рубрики")
    criteria = repos.get_criteria(conn, item.rubric_id)
    if not criteria:
        raise RubricError(f"У рубрики {item.rubric_id} нет активных критериев")

    messages = [
        ChatMessage(role="system", content=GRADER_SYSTEM_PROMPT),
        ChatMessage(
            role="user", content=format_grader_request(item.prompt, criteria, answer)
        ),
    ]
    verdict = await client.chat_structured(messages, RubricVerdict, model=model)
    return verdict_to_result(criteria, verdict, answer)
