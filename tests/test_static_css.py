"""Таблицы стилей разбираются браузером целиком: незакрытое правило гасит всё после него.

Слияние ветки sanya склеило конец правила `.voice-brief-title` (admin.css) с
началом стилей профиля ученика без закрывающей скобки — «Пульс» ученика
рисовался огромным чёрным кругом, а тесты страниц этого не видели.
"""

from pathlib import Path

import pytest

CSS_DIR = Path(__file__).resolve().parents[1] / "src" / "llm_tutor" / "web" / "static" / "css"


def _strip_comments(text: str) -> str:
    out, pos = [], 0
    while (start := text.find("/*", pos)) != -1:
        out.append(text[pos:start])
        end = text.find("*/", start + 2)
        pos = len(text) if end == -1 else end + 2
    out.append(text[pos:])
    return "".join(out)


@pytest.mark.parametrize("path", sorted(CSS_DIR.glob("*.css")), ids=lambda path: path.name)
def test_css_braces_are_balanced(path: Path) -> None:
    depth = 0
    for number, line in enumerate(_strip_comments(path.read_text(encoding="utf-8")).splitlines(), 1):
        depth += line.count("{") - line.count("}")
        assert depth >= 0, f"{path.name}:{number}: лишняя закрывающая скобка"
        # Правило верхнего уровня (селектор с начала строки) внутри другого
        # правила — признак незакрытой скобки выше. @media/@keyframes не в счёт.
        if line[:1] not in ("", " ", "\t", "}", "@") and line.rstrip().endswith("{"):
            assert depth == 1, f"{path.name}:{number}: правило открыто внутри незакрытого"
    assert depth == 0, f"{path.name}: не закрыто правил: {depth}"
