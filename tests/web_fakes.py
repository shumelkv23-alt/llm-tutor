"""Подставные объекты для тестов веб-слоя (без aiogram)."""


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
