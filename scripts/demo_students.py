"""Демо-данные для показа: двое учеников проходят занятия через настоящее приложение.

Работает без ключа OpenRouter: вместо модели стоит заглушка с готовым текстом.
Всё остальное — настоящее: API, ядро хода, проверка ответов, запись в журнал
``events``, пересчёт оценок и расписание повторений.

    uv run python scripts/demo_students.py data/demo

Создаёт ``data/demo/accounts.sqlite3`` и ``data/demo/users/<id>/mlcourse.sqlite3``.
Чтобы открыть их в приложении (с настоящей моделью), запусти его с путями из
этого каталога — см. ``docs/demo/README.md``.

Ученики (пароль у обоих ``секретный-пароль``):
  ann@example.com   — «Знаю в теории», отвечает верно, закрывает 3 занятия;
  boris@example.com — «С нуля», каждый третий ответ неверный, закрывает 2 занятия.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx

from llm_tutor.config import Settings
from llm_tutor.grader.rubric import RubricVerdict
from llm_tutor.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
COURSE = "mlcourse"
PASSWORD = "секретный-пароль"
LESSON = f"/api/courses/{COURSE}/lesson"
LESSON_TEXT = "Суть шага.\n\n---\n\nПример кода.\n\n---\n\nГде это применяют."
MAX_TURNS = 400


def _answers() -> dict[int, dict]:
    """Задания всех модулей по id: нужны эталонные ответы, чтобы отвечать верно или нет."""
    items: dict[int, dict] = {}
    for seed in sorted((ROOT / "data").glob("seed_topic0*.json")):
        for item in json.loads(seed.read_text(encoding="utf-8"))["items"]:
            items[item["id"]] = item
    return items


class StubLLM:
    """Тьютор-заглушка: фиксированный текст урока, рубрика не засчитывает ничего."""

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        if schema is RubricVerdict:
            return RubricVerdict(criteria=[])
        return schema(reply=LESSON_TEXT, hint_level=0)

    async def chat(self, messages, *, model=None, temperature=None) -> str:
        return LESSON_TEXT

    async def aclose(self) -> None:
        pass


class NullMailer:
    async def send(self, *, to: str, subject: str, text: str) -> None:
        pass

    async def aclose(self) -> None:
        pass


async def _run_student(
    client: httpx.AsyncClient, items: dict[int, dict], *, email: str, name: str,
    survey: list[int], is_correct, lessons: int,
) -> int:
    """Регистрирует ученика, проходит анкету и учится, пока не закроет ``lessons`` занятий."""
    await client.post("/api/auth/logout", json={})
    response = await client.post(
        "/api/auth/register", json={"email": email, "name": name, "password": PASSWORD}
    )
    response.raise_for_status()
    (await client.post(f"/api/courses/{COURSE}/enroll", json={})).raise_for_status()
    (await client.post(f"{LESSON}/survey/finish", json={"answers": survey})).raise_for_status()

    answered = 0
    for _ in range(MAX_TURNS):
        state = (await client.get(f"{LESSON}/state")).json()["state"]
        if state["closed"] >= lessons:
            break
        item = state.get("item")
        if item:
            seed = items[item["id"]]
            ok = is_correct(answered)
            answered += 1
            if item["runnable"]:
                body = {"item_id": item["id"], "code": "result = 1", "passed": ok,
                        "report": "" if ok else "AssertionError"}
                response = await client.post(f"{LESSON}/code-result", json=body)
            else:
                if seed["answer_type"] == "choice":
                    right = int(seed["answer"])
                    answer = right if ok else (right + 1) % len(item["options"])
                else:
                    answer = seed.get("answer", "x") if ok else "не знаю"
                response = await client.post(
                    f"{LESSON}/answer", json={"item_id": item["id"], "answer": answer}
                )
        elif state.get("lesson_button"):
            button = state["lesson_button"]
            response = await client.post(
                f"{LESSON}/next", json={"node_id": button["node_id"], "part": button["part"]}
            )
        else:
            response = await client.post(f"{LESSON}/chat", json={"text": "Продолжаем"})
        response.raise_for_status()
    return answered


async def main(out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        openrouter_api_key="demo",
        accounts_db_path=str(out / "accounts.sqlite3"),
        users_dir=str(out / "users"),
        materials_db_path=str(out / "materials.sqlite3"),
        courses_dir=str(out / "courses"),
    )
    items = _answers()
    app = create_app(settings, client=StubLLM(), mailer=NullMailer())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://demo"
        ) as client:
            ann = await _run_student(
                client, items, email="ann@example.com", name="Аня",
                survey=[1, 1, 1, 1, 1], is_correct=lambda n: True, lessons=3,
            )
            boris = await _run_student(
                client, items, email="boris@example.com", name="Борис",
                survey=[0], is_correct=lambda n: (n + 1) % 3 != 0, lessons=2,
            )
    finally:
        app.state.accounts.close()
    print(f"Аня: {ann} ответов, Борис: {boris} ответов. Данные — {out}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "demo"
    asyncio.run(main(target))
