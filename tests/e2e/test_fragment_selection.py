"""Панель вопросов над выделенным фрагментом (срез 78)."""

import pytest
from playwright.async_api import expect

from helpers import enroll, finish_survey_via_api, sign_up, take_or_skip

pytestmark = pytest.mark.e2e

BAR = ".selection-bar"


async def _open(page, live_server):
    violations: list[str] = []
    page.on(
        "console",
        lambda message: violations.append(message.text)
        if "Content Security Policy" in message.text
        else None,
    )
    page.csp = violations
    await sign_up(page, live_server)
    await enroll(page, live_server)
    await finish_survey_via_api(page)
    return page


@pytest.fixture
async def lesson(page, live_server):
    return await _open(page, live_server)


def _clean(page) -> None:
    assert page.errors == []
    assert page.csp == []


async def _select(page, selector: str) -> None:
    """Выделить содержимое первого элемента под ``selector`` (как мышью)."""
    await page.evaluate(
        """selector => {
            const node = document.querySelector(selector);
            const range = document.createRange();
            range.selectNodeContents(node);
            const selection = document.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }""",
        selector,
    )


def _chat_requests(page) -> list:
    sent: list = []
    page.on(
        "request",
        lambda request: sent.append(request)
        if request.method == "POST" and request.url.endswith("/lesson/chat")
        else None,
    )
    return sent


async def _idle(page) -> None:
    await page.wait_for_function("() => !Lesson.busy")


async def test_theory_simpler_sends_one_chat(lesson) -> None:
    page = lesson
    sent = _chat_requests(page)
    await _select(page, "#tab-theory .theory-body p")
    expected = (await page.evaluate("() => getSelection().toString()")).strip()

    await expect(page.locator(BAR)).to_be_visible()
    await page.click(f"{BAR} button:has-text('Проще')")
    await _idle(page)

    assert len(sent) == 1
    body = sent[0].post_data_json
    assert body["text"] == "Объясни этот фрагмент проще."
    assert body["quote"] == {"source": "theory", "text": expected}
    await expect(page.locator(BAR)).to_be_hidden()
    figure = page.locator("#chat-log .msg.user .msg-quote").last
    await expect(figure.locator("figcaption")).to_have_text("Из теории")
    _clean(page)


async def test_editor_ask_attaches_code(lesson) -> None:
    page = lesson
    await page.click("#tab-btn-code")
    await page.wait_for_selector(".CodeMirror")
    await page.evaluate(
        """() => {
            const cm = document.querySelector('.CodeMirror').CodeMirror;
            cm.setValue("df.drop('a', axis=1)\\nprint(df)");
            cm.focus();
            cm.setSelection({line: 0, ch: 0}, {line: 0, ch: 20});
        }"""
    )

    await expect(page.locator(BAR)).to_be_visible()
    await page.click(f"{BAR} button:has-text('Спросить…')")

    await expect(page.locator("#chat-quote")).to_contain_text("Из моего кода")
    await expect(page.locator("#chat-input")).to_be_focused()
    await page.fill("#chat-input", "что делает axis?")
    async with page.expect_request("**/lesson/chat") as request:
        await page.press("#chat-input", "Enter")
    assert (await request.value).post_data_json["quote"] == {
        "source": "code",
        "text": "df.drop('a', axis=1)",
    }
    _clean(page)


async def test_task_explain_sends_task_source(lesson) -> None:
    page = lesson
    await page.click("#tab-btn-practice")
    for _ in range(5):
        if await page.locator(".task-prompt").count():
            break
        await take_or_skip(page)
        await _idle(page)
    await _select(page, "#tab-practice .task-prompt")

    async with page.expect_request("**/lesson/chat") as request:
        await page.click(f"{BAR} button:has-text('Объясни')")

    body = (await request.value).post_data_json
    assert body["text"] == "Объясни этот фрагмент подробнее."
    assert body["quote"]["source"] == "task"
    _clean(page)


async def test_selection_outside_sources_shows_nothing(lesson) -> None:
    page = lesson
    await _select(page, ".chat-head")
    await page.wait_for_timeout(200)
    await expect(page.locator(BAR)).to_be_hidden()


async def test_panel_hides(lesson) -> None:
    page = lesson
    bar = page.locator(BAR)

    await page.focus("#tab-theory")
    await _select(page, "#tab-theory .theory-body p")
    await expect(bar).to_be_visible()
    await page.keyboard.press("Tab")
    await page.keyboard.press("Escape")
    await expect(bar).to_be_hidden()
    assert await page.evaluate("() => document.activeElement.id") == "tab-theory"
    assert await page.evaluate("() => getSelection().toString().trim().length") > 0

    await _select(page, "#tab-theory .theory-body p")
    await expect(bar).to_be_visible()
    await page.evaluate("() => getSelection().removeAllRanges()")
    await expect(bar).to_be_hidden()

    await _select(page, "#tab-theory .theory-body p")
    await expect(bar).to_be_visible()
    await page.click("#tab-btn-practice")
    await expect(bar).to_be_hidden()

    # Перерисовка теории после хода: панель не висит над пустым местом.
    await page.click("#tab-btn-theory")
    await _select(page, "#tab-theory .theory-body p")
    await expect(bar).to_be_visible()
    await page.evaluate("() => Lesson.load()")
    await expect(bar).to_be_hidden()
    _clean(page)


async def test_buttons_disabled_during_turn(lesson) -> None:
    page = lesson
    await page.evaluate("() => { Lesson.chat.ask('привет'); }")
    await page.wait_for_function("() => Lesson.busy")
    await _select(page, "#tab-theory .theory-body p")

    buttons = page.locator(f"{BAR} button")
    await expect(page.locator(BAR)).to_be_visible()
    for index in range(await buttons.count()):
        await expect(buttons.nth(index)).to_be_disabled()
    await _idle(page)
    await expect(buttons.first).to_be_enabled()


async def test_keyboard_tab_reaches_panel(lesson) -> None:
    page = lesson
    await page.focus("#tab-theory")
    await _select(page, "#tab-theory .theory-body p")
    await expect(page.locator(BAR)).to_be_visible()

    await page.keyboard.press("Tab")
    assert await page.evaluate("() => document.activeElement.textContent") == "Объясни"
    await page.keyboard.press("ArrowRight")
    assert await page.evaluate("() => document.activeElement.textContent") == "Проще"
    toolbar = page.locator(f"{BAR}[role=toolbar]")
    await expect(toolbar).to_have_attribute("aria-label", "Спросить тьютора о выделенном")


async def test_panel_stays_inside_window(lesson) -> None:
    page = lesson
    await _select(page, "#tab-theory .theory-body p")
    box = await page.locator(BAR).bounding_box()
    viewport = page.viewport_size

    assert box["x"] >= 8 and box["x"] + box["width"] <= viewport["width"] - 8
    assert box["y"] >= 8 and box["y"] + box["height"] <= viewport["height"] - 8


async def test_phone_switches_to_chat(page, live_server) -> None:
    await page.set_viewport_size({"width": 390, "height": 844})
    page = await _open(page, live_server)
    await _select(page, "#tab-theory .theory-body p")

    await page.click(f"{BAR} button:has-text('Пример')")

    await expect(page.locator("#lesson")).to_have_attribute("data-view", "chat")
    await _idle(page)
    await page.evaluate("() => Lesson.chat.attach({source: 'code', text: 'x'.repeat(300)})")
    await page.fill("#chat-input", "что это?")
    await page.press("#chat-input", "Enter")
    await _idle(page)
    assert await page.evaluate("() => document.documentElement.scrollWidth") <= 390
    _clean(page)


async def test_scroll_hides_only_when_selection_moves(lesson) -> None:
    page = lesson
    bar = page.locator(BAR)
    # Чату есть что прокручивать.
    for _ in range(3):
        await page.evaluate("() => Lesson.chat.ask('расскажи подробнее')")
        await _idle(page)
    await _select(page, "#tab-theory .theory-body p")
    await expect(bar).to_be_visible()

    await page.evaluate("() => { const log = document.getElementById('chat-log'); log.scrollTop = 0; }")
    await page.wait_for_timeout(100)
    await expect(bar).to_be_visible()

    moved = await page.evaluate(
        """() => {
            let node = document.querySelector('#tab-theory .theory-body p').parentElement;
            while (node && node !== document.body) {
                if (node.scrollHeight > node.clientHeight + 40
                    && getComputedStyle(node).overflowY !== 'visible') {
                    node.scrollTop += 60;
                    return true;
                }
                node = node.parentElement;
            }
            window.scrollBy(0, 60);
            return window.scrollY > 0;
        }"""
    )
    assert moved
    await expect(bar).to_be_hidden()


# --- аудит среза 78 ---


async def _active_text(page) -> str:
    return await page.evaluate("() => document.activeElement.textContent")


async def _select_in_editor(page) -> None:
    await page.click("#tab-btn-code")
    await page.evaluate(
        """() => {
            const cm = Lesson.code.editor;
            cm.setValue("for i in range(3):\\n    print(i)\\n");
            cm.focus();
            cm.setSelection({line: 1, ch: 0}, {line: 1, ch: 12});
        }"""
    )
    await expect(page.locator(BAR)).to_be_visible()


async def test_alt_f10_from_editor_keeps_focus_in_panel(lesson) -> None:
    """I-1: CodeMirror не забирает фокус обратно; Esc возвращает его в редактор."""
    page = lesson
    await _select_in_editor(page)

    await page.keyboard.press("Alt+F10")
    await page.wait_for_timeout(100)
    assert await _active_text(page) == "Объясни"
    await expect(page.locator(BAR)).to_be_visible()

    await page.keyboard.press("Escape")
    assert await page.evaluate("() => Boolean(document.activeElement.closest('.CodeMirror'))")


async def test_alt_f10_after_escape_brings_panel_back(lesson) -> None:
    """M-3: Esc спрятал панель, выделение осталось — Alt+F10 возвращает её."""
    page = lesson
    await page.focus("#tab-theory")
    await _select(page, "#tab-theory .theory-body p")
    await page.keyboard.press("Tab")
    await page.keyboard.press("Escape")
    await expect(page.locator(BAR)).to_be_hidden()

    await page.keyboard.press("Alt+F10")
    await expect(page.locator(BAR)).to_be_visible()
    assert await _active_text(page) == "Объясни"


async def test_triple_click_on_task_prompt_shows_panel(lesson) -> None:
    """I-2: тройной клик тянет конец выделения в вариант ответа — хвост пустой."""
    page = lesson
    sent = _chat_requests(page)
    await page.click("#tab-btn-practice")
    for _ in range(4):
        if await page.locator("#tab-practice .task-prompt").count():
            break
        await take_or_skip(page)
    prompt = page.locator("#tab-practice .task-prompt").first
    await prompt.click(click_count=3)

    await expect(page.locator(BAR)).to_be_visible()
    await page.click(f"{BAR} button:has-text('Объясни')")
    await _idle(page)
    quote = sent[-1].post_data_json["quote"]
    assert quote["source"] == "task"
    assert quote["text"] == (await prompt.inner_text()).strip()


async def test_selection_ending_in_other_text_shows_nothing(lesson) -> None:
    page = lesson
    await page.evaluate(
        """() => {
            const range = document.createRange();
            range.setStart(document.querySelector('#tab-theory .theory-body p'), 0);
            range.setEnd(document.querySelector('.chat-head'), 1);
            const selection = document.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    await page.wait_for_timeout(150)
    await expect(page.locator(BAR)).to_be_hidden()


async def test_code_block_buttons_stay_out_of_quote(lesson) -> None:
    """M-1: подписи «Скопировать» / «Открыть в редакторе» не уходят модели."""
    page = lesson
    sent = _chat_requests(page)
    await _select(page, "#tab-theory .theory-body .code-block")
    await page.click(f"{BAR} button:has-text('Объясни')")
    await _idle(page)

    text = sent[-1].post_data_json["quote"]["text"]
    assert "Скопировать" not in text and "Открыть в редакторе" not in text
    assert text.strip()


async def test_long_selection_is_clipped_and_accepted(lesson) -> None:
    page = lesson
    responses: list[int] = []
    page.on("response", lambda r: responses.append(r.status) if r.url.endswith("/lesson/chat") else None)
    await _select(page, "#tab-theory .theory-body")
    await page.click(f"{BAR} button:has-text('Проще')")
    await _idle(page)

    assert responses == [200]


async def test_keyboard_choice_keeps_focus_off_body(lesson) -> None:
    """I-3: после готовой кнопки с клавиатуры фокус не теряется на body."""
    page = lesson
    await page.focus("#tab-theory")
    await _select(page, "#tab-theory .theory-body p")
    await page.keyboard.press("Tab")
    await page.keyboard.press("Enter")
    await _idle(page)

    assert await page.evaluate("() => document.activeElement !== document.body")


async def test_ctrl_a_in_theory_selects_only_theory(lesson) -> None:
    """M-2: с клавиатуры текст выделяется Ctrl+A — только конспект, не страница."""
    page = lesson
    await page.focus("#tab-theory")
    await page.keyboard.press("Control+a")

    await expect(page.locator(BAR)).to_be_visible()
    inside = await page.evaluate(
        """() => {
            const range = getSelection().getRangeAt(0);
            const root = document.querySelector('#tab-theory .theory-body');
            return root.contains(range.startContainer) && root.contains(range.endContainer)
                && range.toString().trim().length > 100;
        }"""
    )
    assert inside


async def test_panel_flips_above_at_bottom_edge(lesson) -> None:
    page = lesson
    await page.evaluate(
        """() => {
            const p = document.querySelector('#tab-theory .theory-body p');
            p.scrollIntoView({block: 'end'});
        }"""
    )
    await _select(page, "#tab-theory .theory-body p")
    await expect(page.locator(BAR)).to_be_visible()
    bar = await page.locator(BAR).bounding_box()
    rect = await page.evaluate("() => { const r = getSelection().getRangeAt(0).getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; }")
    height = await page.evaluate("() => innerHeight")

    assert bar["y"] + bar["height"] <= height
    assert bar["y"] + bar["height"] <= rect["top"] or bar["y"] >= rect["bottom"]


async def test_phone_lesson_has_no_horizontal_scroll(page, live_server) -> None:
    await page.set_viewport_size({"width": 390, "height": 844})
    await _open(page, live_server)
    assert await page.evaluate("() => document.documentElement.scrollWidth") <= 390
