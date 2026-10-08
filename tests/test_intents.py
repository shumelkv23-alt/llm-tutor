"""Тесты распознавания намерения из текста (Срез 15)."""

import pytest

from llm_tutor.core.intents import INTENT_MAX_WORDS, detect, normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("закрой тему", "close_topic"),
        ("Закрой тему!", "close_topic"),
        ("закрой тему пожалуйста", "close_topic"),
        ("давай закроем", "close_topic"),
        ("тема закрыта", "close_topic"),
        ("я это знаю", "close_topic"),
        ("я это уже знаю", "close_topic"),
        ("Я всё знаю", "close_topic"),
        ("пропусти", "skip"),
        ("Пропусти это задание", "skip"),
        ("давай пропустим", "skip"),
        ("скип", "skip"),
        ("не понял", "stuck"),
        ("Не поняла(", "stuck"),
        ("не понимаю", "stuck"),
        ("ничего не понял", "stuck"),
        ("непонятно", "stuck"),
        ("объясни подробнее", "stuck"),
    ],
)
def test_detects_intent(text: str, expected: str) -> None:
    assert detect(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "2",
        "groupby группирует строки по ключу",
        "спасибо, дальше сам",
    ],
)
def test_ordinary_replies_have_no_intent(text: str) -> None:
    """Обычная короткая реплика — не команда."""
    assert detect(text) is None


def test_long_answer_with_intent_words_is_not_intercepted() -> None:
    """Длинный открытый ответ со словами-триггерами остаётся ответом."""
    text = "я не понял в прошлый раз, но сейчас вижу что groupby группирует строки"
    assert len(text.split()) > INTENT_MAX_WORDS
    assert detect(text) is None


def test_close_wins_over_stuck() -> None:
    """При нескольких совпадениях приоритет: close > skip > stuck."""
    assert detect("закрой тему, я не понял") == "close_topic"


def test_normalize_strips_case_punctuation_and_yo() -> None:
    assert normalize("  Закрой ТЕМУ!!!  ") == "закрой тему"
    assert normalize("я всё знаю") == "я все знаю"
    assert normalize("не   понял?") == "не понял"
