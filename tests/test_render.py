"""Тесты слоя представления бота."""

from llm_tutor.bot import menu
from llm_tutor.bot.render import (
    MAX_MESSAGE,
    PARSE_MODE,
    escape,
    fit,
    render_help,
    render_status,
)
from llm_tutor.course.seed import load_seed
from llm_tutor.db import repos
from llm_tutor.schemas import SessionState


def test_escape_neutralizes_model_markup() -> None:
    """Разметка из текста модели показывается буквально, а не как HTML."""
    assert escape("<b>x</b> & <i>y</i>") == "&lt;b&gt;x&lt;/b&gt; &amp; &lt;i&gt;y&lt;/i&gt;"


def test_parse_mode_is_html() -> None:
    assert PARSE_MODE == "HTML"


def test_fit_keeps_short_text_intact() -> None:
    assert fit("коротко") == "коротко"


def test_fit_does_not_break_html_entity() -> None:
    """Обрезка не рвёт сущность вида &amp; — иначе Telegram отвергнет HTML."""
    # «<» → «&lt;»: обрезка попадает в середину последней сущности, поэтому
    # срабатывает ветка отката до последнего «&» (а не точная граница).
    out = fit(escape("<" + "a" * 3995 + "<" * 2))
    body = out.rstrip("…")
    assert len(out) <= MAX_MESSAGE + 1          # + многоточие
    assert body.count("&") == body.count(";")   # ни одной обрубленной сущности
    assert "&lt;" in body                       # сущность до места обрезки цела


def test_render_status_shows_current_node_and_phase(conn, settings) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="groupby", phase="practice", hint_level=1),
    )

    text = render_status(conn, now=0.0, settings=settings)

    assert "groupby" in text
    assert "практика" in text
    assert "подсказк" in text.lower()


def test_render_status_shows_route_progress(conn, settings) -> None:
    """Статус показывает строку прогресса маршрута «пройдено N из M»."""
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn, session_id, SessionState(current_node_id="groupby")
    )

    text = render_status(conn, now=0.0, settings=settings)

    assert "пройдено" in text
    assert " из " in text


def test_render_status_mentions_waiting_task(conn, settings) -> None:
    """При висящем задании статус говорит, что ждёт ответа."""
    load_seed(conn)
    item = repos.get_item(conn, 6)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(current_node_id="groupby", pending_item_id=item.id),
    )

    text = render_status(conn, now=0.0, settings=settings)

    assert "Ждёт ответа" in text


def test_render_help_lists_menu_actions() -> None:
    text = render_help()
    for label in menu.ACTION_LABELS.values():
        assert label in text
