"""Подставные LLM-клиенты и почта для тестов веба и ядра."""

import re

from llm_tutor.db import repos
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict

# Ссылка восстановления в тексте письма (ею же пользуется e2e-мейлер).
RESET_LINK_RE = re.compile(r"https?://\S+/reset-password\?token=[\w\-]+")


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


class RecordingMailer:
    """Почта, которая никуда не отправляет, а копит письма.

    Письма живут в памяти: запрос в тестах идёт через ``httpx.ASGITransport``,
    который доводит фоновые задачи до конца, — к моменту проверки письмо уже
    здесь. Файловый ящик нужен только e2e, где приложение живёт в своём потоке
    (``tests/e2e/conftest.py``: ``FileMailer``), и у него он свой.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.closed = False

    async def send(self, *, to: str, subject: str, text: str) -> None:
        self.sent.append((to, subject, text))

    def last(self) -> tuple[str, str, str]:
        assert self.sent, "Писем не было"
        return self.sent[-1]

    def last_link(self) -> str:
        """Ссылка восстановления из последнего письма."""
        match = RESET_LINK_RE.search(self.last()[2])
        assert match, f"В письме нет ссылки восстановления:\n{self.last()[2]}"
        return match.group(0)

    def last_token(self) -> str:
        return self.last_link().split("token=", 1)[1]

    async def aclose(self) -> None:
        self.closed = True


class FailingMailer:
    """Почта, у которой отправка всегда падает: запрос ломаться не должен."""

    def __init__(self) -> None:
        self.closed = False

    async def send(self, *, to: str, subject: str, text: str) -> None:
        raise OSError("SMTP недоступен")

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


class ExplainLLM:
    """Модели «Объясни новичку»: оценщик, новичок и наставник по заготовкам.

    Роль — по вызову: ``chat_structured`` со схемой ``ExplainObservation`` —
    оценщик, с ``NoviceReply`` — новичок, ``chat`` — наставник. Заготовки
    выдаются по очереди, последняя повторяется. ``fail`` — роли, которые
    бросают ``LLMError``. Сообщения каждого вызова — в ``calls[роль]``.
    """

    def __init__(
        self,
        *,
        observations=(),
        replies=("Понятно, а почему так?",),
        mentor=("Смотри: это делается так.",),
        fail=frozenset(),
    ) -> None:
        self.observations = list(observations)
        self.replies = list(replies)
        self.mentor = list(mentor)
        self.fail = set(fail)
        self.calls: dict[str, list[list]] = {"evaluator": [], "novice": [], "mentor": []}
        self.models: dict[str, list[str | None]] = {"evaluator": [], "novice": [], "mentor": []}
        # Порядок вызовов по ролям: оценщик зовётся раньше новичка.
        self.order: list[str] = []
        self.closed = False

    @staticmethod
    def _next(queue: list, default):
        if not queue:
            return default
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def _record(self, role: str, messages, model) -> None:
        self.calls[role].append(list(messages))
        self.models[role].append(model)
        self.order.append(role)
        if role in self.fail:
            from llm_tutor.llm.client import LLMError

            raise LLMError(f"{role} недоступен", retryable=True)

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        from llm_tutor.llm.schemas import ExplainObservation, NoviceReply

        if schema is ExplainObservation:
            self._record("evaluator", messages, model)
            return self._next(self.observations, ExplainObservation())
        if schema is NoviceReply:
            self._record("novice", messages, model)
            return NoviceReply(reply=self._next(self.replies, "А почему?"))
        raise AssertionError(f"Неожиданный структурный вызов: {schema.__name__}")

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        self._record("mentor", messages, model)
        return self._next(self.mentor, "Смотри.")

    async def aclose(self) -> None:
        self.closed = True
