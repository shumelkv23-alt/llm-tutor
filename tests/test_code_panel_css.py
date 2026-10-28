"""Вкладка «Код»: редактор не должен сжиматься до высоты окна и обрезать код."""

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "src/llm_tutor/web/static/css/lesson.css"


def _rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS.read_text(encoding="utf-8"))
    assert match, f"нет правила {selector}"
    return match.group(1)


def test_code_panel_children_do_not_shrink():
    # .code-panel — flex-колонка в панели с фиксированной высотой: без flex: none
    # .code-editor (overflow: hidden) сжимался, и длинный код нельзя было листать.
    assert re.search(r"flex:\s*none", _rule(".code-panel > *"))


def test_editor_scrolls_inside_instead_of_growing_forever():
    assert "max-height" in _rule(".code-editor .CodeMirror-scroll")
