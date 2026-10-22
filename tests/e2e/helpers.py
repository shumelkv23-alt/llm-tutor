"""Шаги сценариев e2e."""

import itertools

COURSE = "mlcourse"
_ids = itertools.count(1)


async def sign_up(page, base: str) -> None:
    await page.goto(f"{base}/register")
    await page.fill("input[name=name]", "Аня")
    await page.fill("input[name=email]", f"ann{next(_ids)}@example.com")
    await page.fill("input[name=password]", "секретный-пароль")
    await page.click("button[type=submit]")
    await page.wait_for_url(f"{base}/")


async def enroll(page, base: str) -> None:
    await page.goto(f"{base}/courses")
    await page.click("button.enroll")
    await page.wait_for_url(f"{base}/lesson/{COURSE}")
    await page.wait_for_selector("#lesson[aria-busy=false]")


async def finish_survey_via_api(page) -> None:
    """Анкета через API из страницы: ответ «1» на каждый вопрос."""
    await page.evaluate(
        """async (course) => {
            const post = (path, body) => fetch(path, {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
            const answers = [];
            for (;;) {
                const reply = await (await post(`/api/courses/${course}/lesson/survey/next`, {answers})).json();
                if (!reply.question) break;
                answers.push(1);
            }
            await post(`/api/courses/${course}/lesson/survey/finish`, {answers});
        }""",
        COURSE,
    )
    await page.reload()
    await page.wait_for_selector("#lesson[aria-busy=false]")


async def split_value(page) -> int:
    return int(await page.get_attribute("#splitter", "aria-valuenow"))


async def take_or_skip(page) -> None:
    """Один шаг к нужному заданию: взять задание или пропустить висящее.

    Ждём ответа сервера, а не только снятия «занято»: подтверждение пропуска
    запускает ход асинхронно, и проверка «не занято» могла пройти до его начала.
    """
    if await page.locator(".practice .empty button:has-text('Взять задание')").count():
        async with page.expect_response("**/lesson/task"):
            await page.click(".practice .empty button:has-text('Взять задание')")
    else:
        await page.click(".practice-actions button:has-text('Пропустить')")
        async with page.expect_response("**/lesson/skip"):
            await page.click("#confirm-submit")
    await page.wait_for_selector("#lesson:not(.busy)")


async def survey_ui(page) -> None:
    """Анкета модуля кнопками: «С нуля» на каждый вопрос, затем «Начать урок»."""
    await page.click("text=Поехали")
    for _ in range(10):
        await page.wait_for_selector(".survey-option, .survey-summary")
        if await page.locator(".survey-summary").count():
            break
        await page.locator(".survey-option").first.click()
        await page.wait_for_selector("#survey[aria-busy=false]")
    await page.click("text=Начать урок")
    await page.wait_for_selector("#pane-left:not(.surveying)")


async def show_view(page, view: str) -> None:
    """«Урок»/«Чат» на узком экране; на широком обе панели видны и переключателя нет."""
    switch = page.locator(f"#view-{view}")
    if await switch.is_visible():
        await switch.click()


async def check_ui(page) -> None:
    """Кнопка урока в чате → вопрос раздела во вкладке «Практика»."""
    await show_view(page, "chat")
    async with page.expect_response("**/lesson/next"):
        await page.locator("#lesson-next").click()
    await page.wait_for_selector("#lesson:not(.busy)")
    await show_view(page, "lesson")
    await page.click("#tab-btn-practice")


async def answer_first_option(page) -> None:
    """Первый вариант (в фикстуре занятий он верный) и «Ответить»."""
    await page.locator(".practice-option").first.click()
    async with page.expect_response("**/lesson/answer"):
        await page.click(".practice-answer button[type=submit]")
    await page.wait_for_selector("#lesson:not(.busy)")


async def close_first_lesson(page) -> None:
    """Два раздела и итоговое задание занятия «Series и DataFrame» — занятие закрыто."""
    for _ in range(2):
        await check_ui(page)
        await answer_first_option(page)
    await page.fill("input.practice-input", "final_l1_frames")
    async with page.expect_response("**/lesson/answer"):
        await page.click(".practice-answer button[type=submit]")
    await page.wait_for_selector("#lesson:not(.busy)")


def make_due(users_dir, *concept_ids: str) -> None:
    """Занятия закрыты в маршруте ученика и просрочены на лестнице повторений.

    Пишем в БД ученика напрямую: сервер читает состояние на каждый ход, а чтобы
    дойти до просрочки ходами, пришлось бы ждать сутки.
    """
    from llm_tutor.db import repos
    from llm_tutor.db.connection import get_conn

    conn = get_conn(str(next(users_dir.glob("*/mlcourse.sqlite3"))))
    try:
        session_id = repos.get_open_session(conn)
        state = repos.get_session_state(conn, session_id)
        steps = [
            step.model_copy(update={"status": "closed", "closed_at": 1.0})
            if step.concept_id in concept_ids
            else step
            for step in state.route.steps
        ]
        route = state.route.model_copy(update={"steps": steps})
        repos.update_session_state(conn, session_id, state.model_copy(update={"route": route}))
        for concept_id in concept_ids:
            repos.upsert_review(conn, concept_id, 1, 1.0)
    finally:
        conn.close()
