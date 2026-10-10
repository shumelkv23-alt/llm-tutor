"""Подставные LLM-клиенты для тестов веба и ядра."""

from llm_tutor.db import repos
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict


class SilentLLM:
    """LLM-клиент, к которому обращаться не должны.

    Каркасу и страницам модель не нужна: любой вызов — ошибка теста, а не
    тихий сетевой запрос.
    """

    def __init__(self) -> None:
        self.closed = False

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        raise AssertionError("Модель не должна вызываться в этом тесте")

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        raise AssertionError("Модель не должна вызываться в этом тесте")

    async def aclose(self) -> None:
        self.closed = True


class ScriptedTutor:
    """Тьютор с заготовленными ответами (по одному на ход, последний повторяется).

    Отвечает только на тьюторский ход (схема с полем ``reply``): рубричный
    грейдер и прочие вызовы — ошибка теста.
    """

    def __init__(self, *replies: str, **flags) -> None:
        self.replies = list(replies) or ["Ответ тьютора"]
        self.flags = flags
        self.calls: list[list] = []
        self.closed = False

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        if "reply" not in schema.model_fields:
            raise AssertionError(f"Неожиданный структурный вызов: {schema.__name__}")
        self.calls.append(list(messages))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return schema(reply=reply, **self.flags)

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        raise AssertionError("тьюторский ход идёт через chat_structured")

    async def aclose(self) -> None:
        self.closed = True


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
