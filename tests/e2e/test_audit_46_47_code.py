"""Вкладка «Код»: гонки проверки с ходами и сбои воркера (аудит среза 46).

Pyodide подменён воркером-заглушкой, а задание с кодом — подстановкой в ответы
API: проверяется логика страницы, а не Python и не выбор задания.
"""

import asyncio

import pytest
from helpers import enroll, finish_survey_via_api, sign_up
from playwright.async_api import expect

pytestmark = pytest.mark.e2e

FAKE_WORKER = """
self.onmessage = (event) => {
  const m = event.data;
  if (m.code.includes("#crash")) throw new Error("boom");
  if (m.code.includes("#hang")) return;
  self.postMessage({ type: "started", id: m.id });
  setTimeout(() => self.postMessage({
    type: "result", id: m.id, ok: true, stage: m.tests ? "tests" : "code",
    stdout: "вывод", error: null, passed: m.tests ? true : null,
  }), m.code.includes("#slow") ? 2500 : 50);
};
"""


def _item(item_id: int) -> dict:
    return {
        "id": item_id,
        "type": "code",
        "prompt_html": f"<p>Задание {item_id}</p>",
        "options": [],
        "runnable": True,
        "starter": "result = None",
        "setup": "",
        "tests": "assert True",
    }


@pytest.fixture
async def lesson(page, live_server):
    """Урок с подставным заданием 9001; ``ctx`` меняет задание и задержку чата."""
    ctx = {"item": _item(9001), "chat_delay": 0.0, "posts": []}

    async def patch(route):
        response = await route.fetch()
        if "/chat" in route.request.url:
            await asyncio.sleep(ctx["chat_delay"])
        try:
            data = await response.json()
        except Exception:  # noqa: BLE001 — не JSON: отдаём как есть
            await route.fulfill(response=response)
            return
        state = data.get("state") if isinstance(data, dict) else None
        if isinstance(state, dict) and not state.get("survey"):
            state["item"] = ctx["item"]
        await route.fulfill(response=response, json=data)

    await page.route(
        "**/pyodide-worker.js*",
        lambda route: route.fulfill(body=FAKE_WORKER, content_type="text/javascript"),
    )
    await page.route("**/lesson/*", patch)
    page.on(
        "request",
        lambda rq: (
            ctx["posts"].append(rq.url.rsplit("/", 1)[-1])
            if rq.method == "POST"
            else None
        ),
    )
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    await page.click("#tab-btn-code")
    await expect(page.locator(".code-prompt")).to_contain_text("Задание 9001")
    ctx["posts"].clear()
    return ctx


async def _set(page, code: str) -> None:
    await page.evaluate(
        "code => document.querySelector('.CodeMirror').CodeMirror.setValue(code)", code
    )


async def test_result_for_replaced_item_is_not_sent(page, lesson) -> None:
    await _set(page, "#slow")
    await page.click("button:has-text('Проверить')")
    # Пока код выполняется, ход сменил текущее задание.
    lesson["item"] = _item(9002)
    async with page.expect_response("**/lesson/chat"):
        await page.evaluate("() => { window.Lesson.act('chat', {text: 'привет'}); }")
    await expect(page.locator(".code-prompt")).to_contain_text("Задание 9002")

    await expect(page.locator(".toast")).to_contain_text(
        "Задание сменилось", timeout=5000
    )
    assert "code-result" not in lesson["posts"]


async def test_check_during_turn_is_refused_with_notice(page, lesson) -> None:
    lesson["chat_delay"] = 1.5
    await page.evaluate("() => { window.Lesson.act('chat', {text: 'привет'}); }")
    await page.wait_for_selector("#lesson.busy")
    # force: Playwright ждёт снятия aria-disabled, а живой клик проходит сразу.
    await page.click("button:has-text('Проверить')", force=True)

    await expect(page.locator(".toast")).to_contain_text("Дождитесь ответа тьютора")
    await expect(page.locator(".code-console")).not_to_contain_text(
        "Все проверки пройдены"
    )
    await page.wait_for_selector("#lesson:not(.busy)")
    assert "code-result" not in lesson["posts"]


async def test_result_during_turn_is_sent_after_it(page, lesson) -> None:
    lesson["chat_delay"] = 4.0
    await _set(page, "#slow")
    await page.click("button:has-text('Проверить')")
    await page.evaluate("() => { window.Lesson.act('chat', {text: 'привет'}); }")
    await expect(page.locator(".code-console")).to_contain_text(
        "Все проверки пройдены", timeout=5000
    )
    assert "code-result" not in lesson["posts"]

    # Ход закончился — результат уходит (сервер отклонит подставное задание, это не важно).
    async with page.expect_request("**/lesson/code-result", timeout=8000):
        pass
    assert lesson["posts"].index("chat") < lesson["posts"].index("code-result")


async def test_worker_crash_is_not_a_wrong_answer(page, lesson) -> None:
    await _set(page, "#crash")
    await page.click("button:has-text('Проверить')")
    await expect(page.locator(".code-console")).to_contain_text("Сбой Python")
    await page.wait_for_timeout(300)
    assert "code-result" not in lesson["posts"]

    # Упавший воркер заменён новым: следующий запуск работает.
    await _set(page, "print(1)")
    await page.click("button:has-text('Запустить')")
    await expect(page.locator(".code-console")).to_contain_text("вывод")


async def test_python_load_hang_times_out(page, lesson) -> None:
    await page.clock.install()
    await _set(page, "#hang")
    await page.click("button:has-text('Запустить')")
    await expect(page.locator("button:has-text('Запустить')")).to_be_disabled()

    await page.clock.run_for(91_000)
    await expect(page.locator(".code-console")).to_contain_text("Python не загрузился")
    await expect(page.locator("button:has-text('Запустить')")).to_be_enabled()
    assert "code-result" not in lesson["posts"]
