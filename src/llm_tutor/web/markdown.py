"""Безопасный Markdown → HTML: реплики тьютора и конспекты теории.

Текст модели — недоверенный ввод. Сырой HTML выключен (``html=False``),
картинки не рисуются, ссылки — только ``http(s)``, открываются в новой вкладке
без доступа к нашей странице. Результат — ``Markup``: шаблон и API отдают его
как готовый HTML, повторно не экранируя.
"""

from urllib.parse import urlparse

from markdown_it import MarkdownIt
from markupsafe import Markup, escape
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _validate_link(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme.lower() in _ALLOWED_SCHEMES and bool(parsed.netloc)


# Подсветка на сервере (Pygments): внешних скриптов и правок CSP не нужно.
# Классы токенов — короткие (k, s, n…), цвета задаёт lesson.css.
_FORMATTER = HtmlFormatter(nowrap=True)


def _highlight(code: str, lang: str, attrs: str) -> str:
    """HTML подсвеченного кода; незнакомый язык — просто экранированный текст."""
    try:
        lexer = get_lexer_by_name(lang or "python")
    except ClassNotFound:
        return str(escape(code))
    return pygments_highlight(code, lexer, _FORMATTER)


def _build() -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": False, "breaks": True, "highlight": _highlight},
    )
    md.enable("table")
    md.disable("image")
    md.validateLink = _validate_link

    def link_open(renderer, tokens, idx, options, env):
        token = tokens[idx]
        token.attrSet("target", "_blank")
        token.attrSet("rel", "noopener noreferrer")
        return renderer.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", link_open)
    return md


_MD = _build()


def render(text: str) -> Markup:
    """HTML из Markdown (готовый, безопасный)."""
    if not text:
        return Markup("")
    return Markup(_MD.render(text))
