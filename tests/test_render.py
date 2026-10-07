"""Тесты слоя представления бота."""

from llm_tutor.bot.render import MAX_MESSAGE, PARSE_MODE, escape, fit


def test_escape_neutralizes_model_markup() -> None:
    """Разметка из текста модели показывается буквально, а не как HTML."""
    assert escape("<b>x</b> & <i>y</i>") == "&lt;b&gt;x&lt;/b&gt; &amp; &lt;i&gt;y&lt;/i&gt;"


def test_parse_mode_is_html() -> None:
    assert PARSE_MODE == "HTML"


def test_fit_keeps_short_text_intact() -> None:
    assert fit("коротко") == "коротко"


def test_fit_does_not_break_html_entity() -> None:
    """Обрезка не рвёт сущность вида &amp; — иначе Telegram отвергнет HTML."""
    out = fit(escape("&" * 5000))  # после escape строка в разы длиннее лимита
    body = out.rstrip("…")
    assert len(out) <= MAX_MESSAGE + 1          # + многоточие
    assert body.count("&") == body.count(";")   # ни одной обрубленной сущности
