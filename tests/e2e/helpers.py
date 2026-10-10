"""Шаги сценариев e2e."""

import itertools

COURSE = "mlcourse-topic01"
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
