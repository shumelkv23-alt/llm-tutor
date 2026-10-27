"""E2E «Объясни новичку» внутри занятия: обязательное задание (срез 96)."""

import pytest

from helpers import COURSE, enroll, finish_survey_via_api, prepare_close, show_view, sign_up

pytestmark = pytest.mark.e2e

OTHER_TABS = ("theory", "practice", "code", "review")


async def _novice_messages(page) -> int:
    return await page.locator("#tab-explain .explain-log .msg:not(.pending)").count()


async def _say(page, text: str, expected: int) -> None:
    await page.fill("#explain-input", text)
    await page.press("#explain-input", "Enter")
    await page.wait_for_function(
        "n => document.querySelectorAll('#tab-explain .explain-log .msg:not(.pending)').length >= n",
        arg=expected,
    )
    await page.wait_for_selector("#lesson:not(.busy)")


async def test_required_task_flow(page, explain_server, tmp_path) -> None:
    await sign_up(page, explain_server)
    await enroll(page, explain_server)
    await finish_survey_via_api(page)
    answer = prepare_close(tmp_path / "users", "l1_select")
    await page.reload()
    await page.wait_for_selector("#lesson[aria-busy=false]")

    # Верный ответ на итоговое задание закрывает занятие — курс встаёт.
    await page.click("#tab-btn-practice")
    await page.fill("input.practice-input", answer)
    async with page.expect_response("**/lesson/answer"):
        await page.click(".practice-answer button[type=submit]")
    await page.wait_for_selector("#lesson:not(.busy)")

    # Выбрана вкладка «Объясни», остальные закрыты, правый чат — наставник.
    assert await page.get_attribute("#tab-btn-explain", "aria-selected") == "true"
    for name in OTHER_TABS:
        assert await page.get_attribute(f"#tab-btn-{name}", "aria-disabled") == "true"
    assert "откроется после объяснения" in await page.inner_text("#tab-btn-theory")
    assert "Объясни новичку" in await page.inner_text("#topic-title")
    assert "Обязательное задание" in await page.inner_text("#topic-meta")
    first = page.locator("#tab-explain .explain-log .msg").first
    assert "🐣 Новичок" in await first.inner_text()
    await show_view(page, "chat")
    assert await page.inner_text("#chat-title") == "Наставник"
    await show_view(page, "lesson")

    # Первая реплика новичку — до вопроса наставнику.
    await _say(page, "loc выбирает по меткам индекса, а iloc — по номерам позиций.", 3)
    assert "осталось 1 реплика" in await page.inner_text("#tab-explain .explain-progress")

    # Вопрос наставнику — в правый чат, ветка новичка не растёт.
    await show_view(page, "chat")
    await page.fill("#chat-input", "Не понимаю, чем loc отличается от iloc.")
    await page.press("#chat-input", "Enter")
    await page.wait_for_function(
        "() => document.querySelectorAll('#chat-log .msg.assistant').length >= 1"
    )
    await page.wait_for_selector("#lesson:not(.busy)")
    assert "ok" in await page.locator("#chat-log .msg.assistant").last.inner_text()
    await show_view(page, "lesson")
    assert await _novice_messages(page) == 3

    # Вторая реплика — последняя по лимиту: задание завершается само, виден отчёт.
    await page.fill("#explain-input", "Булева маска оставляет строки, где условие истинно.")
    await page.press("#explain-input", "Enter")
    await page.wait_for_selector("#tab-explain .explain-report")
    report = await page.inner_text("#tab-explain .explain-report")
    assert "Обязательных идей объяснено верно: 0 из 1" in report
    assert "спрашивал наставника" in report
    assert await page.get_attribute("#tab-btn-theory", "aria-disabled") == "true"

    # «Продолжить» открывает вкладки и ведёт к следующему занятию.
    async with page.expect_response("**/lesson/explain/continue"):
        await page.click("#tab-explain button:has-text('Продолжить')")
    await page.wait_for_selector("#lesson:not(.busy)")
    for name in OTHER_TABS:
        assert await page.get_attribute(f"#tab-btn-{name}", "aria-disabled") is None
    assert await page.inner_text("#topic-title") == "Группировка"
    assert await page.get_attribute("#tab-btn-theory", "aria-selected") == "true"
    await show_view(page, "chat")
    assert await page.inner_text("#chat-title") == "Тьютор"

    # Вкладка «Объясни» без задания: история и «Объяснить ещё раз».
    await show_view(page, "lesson")
    await page.click("#tab-btn-explain")
    assert await page.locator("#tab-explain .explain-history li").count() == 1
    assert "Выборка данных" in await page.inner_text("#tab-explain .explain-lessons")
    assert page.errors == []
