"""Фоновые прогоны генерации: задача в процессе, состояние — на диске.

Генерация модуля — это десятки вызовов модели (минуты), а браузер обрывает
запрос через 90 секунд. Поэтому эндпоинт только **запускает** задачу и сразу
отвечает, а прогресс интерфейс опрашивает короткими запросами: состояние лежит
в ``generation/run.json`` курса (``course.build.steps``) и переживает перезапуск
сервера — «прогон прервался, можно продолжить».

Один прогон на курс: второй запуск отвечает 409, пока первый идёт.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from llm_tutor.course.build import steps

logger = logging.getLogger(__name__)

# Что запускаем в фоне: прогон получает «остановиться?» и приёмник прогресса.
Runner = Callable[
    [Callable[[], bool], Callable[[steps.RunState], None]], Awaitable[steps.RunState]
]


@dataclass
class Job:
    """Идущий прогон: задача, флаг отмены и последнее состояние шага."""

    course_id: str
    module: int
    task: asyncio.Task | None = None
    # Список из одного флага: прогон видит его по ссылке, отмена — меняет.
    _cancelled: list[bool] = field(default_factory=lambda: [False])
    state: steps.RunState | None = None

    @property
    def cancelled(self) -> bool:
        """Просили ли остановиться."""
        return self._cancelled[0]

    def stop(self) -> None:
        self._cancelled[0] = True

    def remember(self, state: steps.RunState) -> None:
        """Приёмник прогресса: состояние после очередного шага."""
        self.state = state


class JobRegistry:
    """Прогоны по курсам: по одному на курс."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def running(self, course_id: str) -> Job | None:
        """Идущий прогон курса (``None`` — ничего не идёт)."""
        job = self._jobs.get(course_id)
        if job is None or job.task is None or job.task.done():
            return None
        return job

    def start(self, course_id: str, module: int, runner: Runner) -> Job:
        """Запускает прогон в фоне и возвращает его."""
        job = Job(course_id=course_id, module=module)

        async def run() -> steps.RunState:
            return await runner(lambda: job.cancelled, job.remember)

        job.task = asyncio.create_task(run())
        job.task.add_done_callback(lambda finished: self._finished(course_id, finished))
        self._jobs[course_id] = job
        return job

    def cancel(self, course_id: str) -> bool:
        """Просит прогон остановиться. ``True`` — прогон был и получил просьбу."""
        job = self.running(course_id)
        if job is None:
            return False
        job.stop()
        return True

    def running_courses(self) -> list[str]:
        """Курсы, у которых сейчас идёт прогон."""
        return [course_id for course_id in self._jobs if self.running(course_id) is not None]

    def _finished(self, course_id: str, task: asyncio.Task) -> None:
        """Прогон кончился: ошибку задачи пишем в лог (иначе её никто не прочтёт)."""
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Генерация курса %s упала: %s", course_id, error, exc_info=error)

    def cancel_all(self) -> None:
        """Остановка приложения: задачи не должны пережить цикл событий."""
        for job in self._jobs.values():
            job.stop()
            if job.task is not None:
                job.task.cancel()
        self._jobs.clear()
