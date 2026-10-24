"""Промпты и пороги «Объясни новичку» из TOML (срез 85).

Тексты персонажа, оценщика и наставника правятся в ``data/prompts/explain.toml``
без изменения кода. Файл читается один раз при старте приложения и
проверяется целиком: нет ключа, лишняя подстановка или число вне границ —
``PromptConfigError`` при запуске, а не сбой на ходу ученика.

Каждый текст проходит через ``str.format``: подстановки — ``{имя}``, буквальная
фигурная скобка — удвоенная. Набор подстановок у каждого текста свой
(``ALLOWED_FIELDS``).
"""

import string
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FILE_NAME = "explain.toml"


class PromptConfigError(ValueError):
    """Файл промптов не читается или не проходит проверку."""


class _Strict(BaseModel):
    # Опечатка в ключе — ошибка, а не молча забытый текст.
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoviceDirectives(_Strict):
    """Установки хода новичка: выбирает код, текст — отсюда."""

    ask: str = Field(min_length=1)
    pitfall: str = Field(min_length=1)
    own_words: str = Field(min_length=1)
    confused: str = Field(min_length=1)
    no_answer: str = Field(min_length=1)
    offer_mentor: str = Field(min_length=1)
    wrap_up: str = Field(min_length=1)
    strict: str = Field(min_length=1)


class NovicePrompts(_Strict):
    system: str = Field(min_length=1)
    opening: str = Field(min_length=1)
    fallback_questions: list[str] = Field(min_length=1)
    directives: NoviceDirectives


class RolePrompt(_Strict):
    system: str = Field(min_length=1)


class ExplainLimits(_Strict):
    """Пороги режима; значения по умолчанию — стартовые из спеки."""

    paste_min_chars: int = Field(default=600, ge=1)
    paste_overlap: float = Field(default=0.5, gt=0.0, le=1.0)
    wrap_up_share: float = Field(default=0.8, gt=0.0, le=1.0)
    max_student_turns: int = Field(default=30, ge=1)
    pitfall_every: int = Field(default=3, ge=1)
    pitfall_max: int = Field(default=3, ge=1)
    novice_max_chars: int = Field(default=600, ge=1)
    stale_hours: float = Field(default=24.0, gt=0.0)
    tail_turns: int = Field(default=6, ge=1)


class ExplainPrompts(_Strict):
    novice: NovicePrompts
    evaluator: RolePrompt
    mentor: RolePrompt
    limits: ExplainLimits = Field(default_factory=ExplainLimits)


# Какие подстановки принимает каждый текст (путь — как в TOML).
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "novice.system": frozenset({"title", "known", "lessons"}),
    "novice.opening": frozenset({"title"}),
    "novice.directives.ask": frozenset({"lesson"}),
    "novice.directives.pitfall": frozenset({"lesson", "belief"}),
    "novice.directives.confused": frozenset({"contradiction"}),
}


def _texts(prompts: ExplainPrompts) -> dict[str, str]:
    """Все тексты конфига по путям TOML."""
    texts = {
        "novice.system": prompts.novice.system,
        "novice.opening": prompts.novice.opening,
        "evaluator.system": prompts.evaluator.system,
        "mentor.system": prompts.mentor.system,
    }
    for name, value in prompts.novice.directives.model_dump().items():
        texts[f"novice.directives.{name}"] = value
    for index, value in enumerate(prompts.novice.fallback_questions):
        texts[f"novice.fallback_questions[{index}]"] = value
    return texts


def _check_fields(prompts: ExplainPrompts) -> None:
    for path, text in _texts(prompts).items():
        allowed = ALLOWED_FIELDS.get(path, frozenset())
        try:
            fields = {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
        except ValueError as error:  # непарная фигурная скобка
            raise PromptConfigError(f"{FILE_NAME}: {path}: {error}") from error
        unknown = sorted(fields - allowed)
        if unknown:
            raise PromptConfigError(
                f"{FILE_NAME}: {path}: подстановки {unknown} не поддерживаются "
                f"(можно: {sorted(allowed) or 'никаких'})"
            )


def load_explain(directory: Path) -> ExplainPrompts:
    """Читает и проверяет ``<directory>/explain.toml``."""
    path = Path(directory) / FILE_NAME
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as error:
        raise PromptConfigError(f"Нет файла промптов {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise PromptConfigError(f"{path}: {error}") from error
    try:
        prompts = ExplainPrompts.model_validate(raw)
    except ValidationError as error:
        raise PromptConfigError(f"{path}: {error}") from error
    _check_fields(prompts)
    return prompts
