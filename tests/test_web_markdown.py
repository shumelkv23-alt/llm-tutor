"""Безопасный Markdown для реплик тьютора и конспектов (Срез 41)."""

import pytest
from markupsafe import Markup

from llm_tutor.web.markdown import render


@pytest.mark.parametrize(
    "text",
    [
        "<script>alert(1)</script>",
        '<img src=x onerror="alert(1)">',
        "<iframe src='https://evil.example'></iframe>",
    ],
)
def test_raw_html_is_shown_as_text(text: str) -> None:
    html = render(text)

    assert "<script" not in html
    assert "<img" not in html
    assert "<iframe" not in html
    assert "&lt;" in html


@pytest.mark.parametrize(
    "text",
    [
        "[клик](javascript:alert(1))",
        "[клик](JaVaScRiPt:alert(1))",
        "[клик](data:text/html;base64,PHNjcmlwdD4=)",
        "[клик](vbscript:msgbox)",
        "[клик](//evil.example/x)",
    ],
)
def test_dangerous_links_are_not_links(text: str) -> None:
    html = render(text)

    assert "href" not in html


def test_http_links_open_safely() -> None:
    html = render("[mlcourse](https://mlcourse.ai/book)")

    assert 'href="https://mlcourse.ai/book"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html


def test_images_are_not_rendered() -> None:
    html = render("![x](https://evil.example/pixel.png)")

    assert "<img" not in html


def test_code_blocks_lists_and_emphasis() -> None:
    html = render("**жирный** и `df.head()`\n\n- раз\n- два\n\n```python\ndf.groupby('city')\n```")

    assert "<strong>жирный</strong>" in html
    assert "<code>df.head()</code>" in html
    assert "<li>раз</li>" in html
    assert '<code class="language-python">' in html
    # Подсветка Pygments: токены в span, строки экранированы.
    assert '<span class="n">groupby</span>' in html


def test_code_inside_block_is_escaped() -> None:
    html = render("```python\nx = '<script>alert(1)</script>'\n```")

    assert "<script" not in html
    assert "&lt;script&gt;" in html


def test_unknown_language_is_plain_escaped_text() -> None:
    html = render("```нетакогоязыка\n<b>x</b>\n```")

    assert "<b>" not in html
    assert "&lt;b&gt;" in html


def test_tables_render() -> None:
    html = render("| a | b |\n|---|---|\n| 1 | 2 |")

    assert "<table>" in html


def test_result_is_markup_and_newlines_are_kept() -> None:
    html = render("строка один\nстрока два")

    assert isinstance(html, Markup)
    assert "<br" in html


def test_empty_text() -> None:
    assert render("") == ""
