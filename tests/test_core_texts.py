"""Тексты ядра не привязаны к интерфейсу (Срез 40).

Ответы ``core`` показывает браузер: команды бота вроде ``/task`` или
кнопка «🎚 Темы» ученику веба ничего не значат.
"""

import re

import pytest

from llm_tutor.core import turn, verify
from llm_tutor.llm import prompts

_COMMAND_RE = re.compile(r"(?<![\w.])/[a-z]+\b")


def _texts(module) -> list[tuple[str, str]]:
    return [
        (name, value)
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str)
    ]


@pytest.mark.parametrize("module", [turn, verify, prompts])
def test_core_texts_do_not_mention_bot_commands(module) -> None:
    for name, value in _texts(module):
        assert not _COMMAND_RE.search(value), (name, value)
        assert "🎚" not in value, name
