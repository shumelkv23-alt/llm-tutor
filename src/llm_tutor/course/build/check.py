"""Проверка код-заданий модуля: тесты должны работать.

Сгенерированный код-тест до сих пор никто не запускал: валидация seed смотрит
только, что поля заполнены, а работают ли проверки — видно лишь тогда, когда
задание откроет ученик. Здесь мы прогоняем задания сами:

- ``tests`` обязаны **падать** на заготовке ``starter`` (иначе задание
  засчитается само собой);
- ``tests`` обязаны **проходить** на эталонном решении (иначе верное решение
  будет считаться неверным).

Код пришёл от модели, поэтому запускаем его **отдельным процессом с таймаутом**,
а не в процессе приложения.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from llm_tutor.course.build import edit
from llm_tutor.course.seed import _parse_seed

# Сколько ждём одно задание: в браузере у ученика тот же порядок (10 секунд).
RUN_TIMEOUT = 12.0
# Библиотеки, без которых задание проверить нельзя (в браузере они есть,
# на сервере — только в dev-установке).
NEEDED_PACKAGES = ("pandas", "numpy")


@dataclass(frozen=True)
class CodeProblem:
    """Задание, которое не проверилось, и что именно не так."""

    item_id: int
    prompt: str
    kind: str
    detail: str

    def as_text(self) -> str:
        return f"задание {self.item_id} («{self.prompt[:60]}»): {self.kind} — {self.detail}"


# Запускаемый код: читает исходник из stdin, выполняет и сообщает итог.
_RUNNER = """
import json, sys
source = json.loads(sys.stdin.read())
try:
    exec(compile(source, "<task>", "exec"), {})
except SystemExit:
    raise
except BaseException as error:  # noqa: BLE001 — важен любой сбой задания
    print("FAIL:" + type(error).__name__ + ": " + str(error)[:300])
    sys.exit(1)
print("OK")
"""


def _missing_packages() -> list[str]:
    """Библиотеки, которых нет на сервере: с ними задание не проверить."""
    import importlib.util

    return [name for name in NEEDED_PACKAGES if importlib.util.find_spec(name) is None]


def run_task(setup: str, code: str, tests: str, *, timeout: float = RUN_TIMEOUT) -> tuple[bool, str]:
    """Прогоняет ``setup → code → tests``. Возвращает ``(прошло, объяснение)``."""
    source = "\n\n".join(part for part in (setup or "", code or "", tests or "") if part.strip())
    if not source.strip():
        return False, "в задании нет кода для запуска"
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _RUNNER],
            input=json.dumps(source),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"не уложилось в {int(timeout)} секунд"
    if completed.returncode == 0 and completed.stdout.strip().endswith("OK"):
        return True, ""
    reason = ""
    for line in completed.stdout.splitlines():
        if line.startswith("FAIL:"):
            reason = line[len("FAIL:") :].strip()
    if not reason:
        reason = (completed.stderr or "").strip().splitlines()[-1:] and (
            (completed.stderr or "").strip().splitlines()[-1][:200]
        ) or "неизвестная ошибка"
    return False, reason


def check_module(root: str | Path, module: int) -> list[CodeProblem]:
    """Проверяет все код-задания модуля (включая итоговое).

    Пустой список — либо все задания рабочие, либо проверять нечем (нет нужных
    библиотек: тогда честно скажем об этом одним предупреждением).
    """
    path = edit.module_path(root, module)
    if not path.is_file():
        return []
    missing = _missing_packages()
    seed = _parse_seed(path)
    problems: list[CodeProblem] = []
    for item in seed.items:
        if item.answer_type != "code" or not item.tests:
            continue
        if missing:
            problems.append(
                CodeProblem(item.id, item.prompt, "не проверено", f"на сервере нет {', '.join(missing)}")
            )
            continue
        if not item.solution:
            problems.append(
                CodeProblem(item.id, item.prompt, "нет эталонного решения", "проверить нечем")
            )
            continue
        ok, reason = run_task(item.setup or "", item.solution, item.tests)
        if not ok:
            problems.append(
                CodeProblem(item.id, item.prompt, "тесты не проходят на решении", reason)
            )
            continue
        passed_on_starter, _ = run_task(item.setup or "", item.starter or "", item.tests)
        if passed_on_starter:
            problems.append(
                CodeProblem(
                    item.id,
                    item.prompt,
                    "тесты проходят на заготовке",
                    "задание засчитается без работы",
                )
            )
    return problems
