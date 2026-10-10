"""Страница занятия в браузере: сплит 70/30, ползунок, чат (Срез 42)."""

import pytest

from helpers import enroll, finish_survey_via_api, sign_up, split_value

pytestmark = pytest.mark.e2e


@pytest.fixture
async def lesson(page, live_server):
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    return page


async def test_split_starts_at_70_30(lesson) -> None:
    left = await lesson.eval_on_selector("#pane-left", "e => e.getBoundingClientRect().width")
    total = await lesson.eval_on_selector("#split", "e => e.getBoundingClientRect().width")

    assert await split_value(lesson) == 70
    assert abs(left / total - 0.70) < 0.01


async def test_drag_keys_limits_and_reset(lesson) -> None:
    box = await lesson.locator("#splitter").bounding_box()
    y = box["y"] + box["height"] / 2
    await lesson.mouse.move(box["x"] + box["width"] / 2, y)
    await lesson.mouse.down()
    await lesson.mouse.move(box["x"] - 2000, y, steps=5)
    await lesson.mouse.up()
    assert await split_value(lesson) == 45

    await lesson.focus("#splitter")
    for _ in range(40):
        await lesson.keyboard.press("ArrowRight")
    widest = await split_value(lesson)
    chat = await lesson.eval_on_selector("#pane-chat", "e => e.getBoundingClientRect().width")
    assert widest <= 80
    assert chat >= 279

    await lesson.keyboard.press("Home")
    assert await split_value(lesson) == 45

    await lesson.dblclick("#splitter")
    assert await split_value(lesson) == 70


async def test_split_is_remembered(lesson) -> None:
    await lesson.focus("#splitter")
    for _ in range(5):
        await lesson.keyboard.press("ArrowLeft")

    await lesson.reload()
    await lesson.wait_for_selector("#lesson[aria-busy=false]")

    assert await split_value(lesson) == 60


async def test_chat_sends_and_renders_safely(lesson) -> None:
    before = await lesson.locator(".msg").count()

    await lesson.fill("#chat-input", "Что такое groupby? <img src=x onerror=alert(1)>")
    await lesson.keyboard.press("Enter")
    await lesson.wait_for_selector("#chat-typing", state="visible")
    await lesson.wait_for_selector("#chat-typing", state="hidden")

    assert await lesson.locator(".msg").count() == before + 2
    assert await lesson.locator(".msg img").count() == 0
    assert await lesson.locator(".msg.assistant pre code").count() >= 1
    assert await lesson.input_value("#chat-input") == ""
    assert lesson.errors == []


async def test_shift_enter_makes_new_line(lesson) -> None:
    await lesson.click("#chat-input")
    await lesson.keyboard.type("раз")
    await lesson.keyboard.press("Shift+Enter")
    await lesson.keyboard.type("два")

    assert await lesson.input_value("#chat-input") == "раз\nдва"


async def test_mobile_switches_between_lesson_and_chat(lesson) -> None:
    await lesson.set_viewport_size({"width": 390, "height": 844})

    assert await lesson.is_visible("#pane-left")
    assert not await lesson.is_visible("#pane-chat")
    assert not await lesson.is_visible("#splitter")
    await lesson.click("#view-chat")
    assert await lesson.is_visible("#pane-chat")
    assert not await lesson.is_visible("#pane-left")
    for width in (390, 320):
        await lesson.set_viewport_size({"width": width, "height": 700})
        assert await lesson.evaluate("document.documentElement.scrollWidth") == width


async def test_panes_fit_the_viewport(lesson) -> None:
    """Длинный конспект прокручивается внутри панели, а не растягивает страницу."""
    viewport = lesson.viewport_size["height"]
    for selector in ("#pane-left", "#pane-chat", "#splitter"):
        box = await lesson.locator(selector).bounding_box()
        assert box["y"] + box["height"] <= viewport + 1, selector
