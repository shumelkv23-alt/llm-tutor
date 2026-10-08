"""Общие подставные объекты для тестов."""

from llm_tutor.db import repos
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict


class GradingTutor:
    """Отвечает и как тьютор, и как грейдер: вердикт задаётся параметром."""

    def __init__(self, conn, *, passed: bool) -> None:
        self.conn = conn
        self.passed = passed

    def _verdict(self) -> RubricVerdict:
        criteria = [
            CriterionVerdict(
                id=criterion.id,
                passed=self.passed,
                quote="groupby" if self.passed else None,
            )
            for rubric in repos.get_rubrics(self.conn)
            for criterion in repos.get_criteria(self.conn, rubric.id)
        ]
        return RubricVerdict(criteria=criteria)

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        if schema is RubricVerdict:
            return self._verdict()
        return schema(reply="Разбираем.", hint_level=0)

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        return "Разбираем."
