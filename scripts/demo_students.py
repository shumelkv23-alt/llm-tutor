"""Демо-данные для показа: двое учеников проходят занятия через настоящее приложение.

Работает без ключа OpenRouter: вместо модели стоит заглушка с готовым текстом.
Всё остальное — настоящее: API, ядро хода, проверка ответов, запись в журнал
``events``, пересчёт оценок, расписание повторений и «Объясни новичку».

    uv run python scripts/demo_students.py                         # демо-курс trees-demo
    uv run python scripts/demo_students.py --course mlcourse
    uv run python scripts/demo_students.py --out data/showcase

Создаёт ``<out>/accounts.sqlite3`` и ``<out>/users/<id>/<курс>.sqlite3``. Чтобы
открыть их в приложении (с настоящей моделью), запусти его с путями из этого
каталога — см. ``docs/demo/SHOWCASE.md``.

Ученики (пароль у обоих ``секретный-пароль``):
  ann@example.com   — «Знаю в теории», отвечает верно, закрывает 2 занятия;
  boris@example.com — «С нуля», каждый третий ответ неверный, закрывает 1 занятие.
Закрыв важное занятие, ученик объясняет его новичку: заглушка-оценщик засчитывает
обязательные идеи, которые ученик назвал дословно, — как и настоящая проверка цитат.
"""

import argparse
import asyncio
import json
import re
import shutil
from pathlib import Path

import httpx

from llm_tutor.config import Settings
from llm_tutor.grader.rubric import RubricVerdict
from llm_tutor.llm.schemas import (
    ExplainObservation,
    IdeaVerdict,
    NoviceReply,
    ProjectMentorReply,
)
from llm_tutor.web import courses
from llm_tutor.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "секретный-пароль"
LESSON_TEXT = "Суть шага.\n\n---\n\nПример кода.\n\n---\n\nГде это применяют."
MAX_TURNS = 400
# Эталон занятия в сообщении оценщику (core/explain.evaluator_messages).
_REFERENCE = re.compile(r"Эталон занятия:\n(\{.*?\n\})", re.DOTALL)


def _seed_dir(course_id: str) -> Path:
    course = courses.get_course(course_id)
    if course is None:
        raise SystemExit(f"Нет курса {course_id}")
    return course.root or course.data_dir or ROOT / "data"


def _seeds(course_id: str) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(_seed_dir(course_id).glob("seed_topic*.json"))
    ]


class StubLLM:
    """Модель-заглушка: урок — готовый текст, рубрика не засчитывает ничего.

    Оценщик «Объясни новичку» засчитывает идею эталона, если её текст есть в реплике
    ученика дословно; новичок отвечает «кажется, понял».
    """

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        if schema is RubricVerdict:
            return RubricVerdict(criteria=[])
        if schema is ExplainObservation:
            text = messages[-1].content
            match = _REFERENCE.search(text)
            ideas = json.loads(match.group(1))["ideas"] if match else []
            return ExplainObservation(ideas=[
                IdeaVerdict(id=idea["id"], verdict="correct", quote=idea["text"])
                for idea in ideas
                if idea["text"] in text.rsplit("Реплика ", 1)[-1]
            ])
        if schema is NoviceReply:
            return NoviceReply(reply="Кажется, понял! Спасибо.")
        if schema is ProjectMentorReply:
            return ProjectMentorReply(reply="Давай по шагам.")
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
    client: httpx.AsyncClient, course_id: str, seeds: list[dict], *, email: str, name: str,
    level: int, block_answer: int | None, is_correct, lessons: int,
) -> int:
    """Регистрирует ученика, проходит анкету и учится, пока не закроет ``lessons`` занятий.

    ``block_answer`` — ответ на каждый блок анкеты (``None`` — уровень «с нуля», блоков нет).
    """
    items = {item["id"]: item for seed in seeds for item in seed["items"]}
    reference = {node["id"]: node["explain"] for seed in seeds for node in seed["nodes"] if node.get("explain")}
    blocks = len(seeds[0]["topic"]["survey"]["blocks"])
    survey = [level] + ([block_answer] * blocks if block_answer is not None else [])
    lesson = f"/api/courses/{course_id}/lesson"

    await client.post("/api/auth/logout", json={})
    response = await client.post(
        "/api/auth/register", json={"email": email, "name": name, "password": PASSWORD}
    )
    response.raise_for_status()
    (await client.post(f"/api/courses/{course_id}/enroll", json={})).raise_for_status()
    (await client.post(f"{lesson}/survey/finish", json={"answers": survey})).raise_for_status()

    answered = 0
    for _ in range(MAX_TURNS):
        state = (await client.get(f"{lesson}/state")).json()["state"]
        explain = state.get("explain") or {}
        active = explain.get("active")
        if explain.get("blocked"):
            if active and active["status"] == "active":
                # Объясняем обязательные идеи их же словами (ученик «выучил конспект»).
                ideas = reference[active["node_id"]]["ideas"]
                text = " ".join(idea["text"] for idea in ideas if idea["required"])
                response = await client.post(f"{lesson}/explain/turn", json={"text": text})
            else:
                # Отчёт готов — «Продолжить»: иначе курс так и стоит на задании.
                response = await client.post(f"{lesson}/explain/continue", json={})
            response.raise_for_status()
            continue
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
                response = await client.post(f"{lesson}/code-result", json=body)
            else:
                if seed["answer_type"] == "choice":
                    right = int(seed["answer"])
                    answer = right if ok else (right + 1) % len(item["options"])
                else:
                    answer = seed.get("answer", "x") if ok else "не знаю"
                response = await client.post(
                    f"{lesson}/answer", json={"item_id": item["id"], "answer": answer}
                )
        elif state.get("lesson_button"):
            button = state["lesson_button"]
            response = await client.post(
                f"{lesson}/next", json={"node_id": button["node_id"], "part": button["part"]}
            )
        else:
            response = await client.post(f"{lesson}/chat", json={"text": "Продолжаем"})
        response.raise_for_status()
    return answered


async def main(out: Path, course_id: str) -> None:
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
    app = create_app(settings, client=StubLLM(), mailer=NullMailer())
    seeds = _seeds(course_id)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://demo"
        ) as client:
            ann = await _run_student(
                client, course_id, seeds, email="ann@example.com", name="Аня",
                level=1, block_answer=1, is_correct=lambda n: True, lessons=2,
            )
            boris = await _run_student(
                client, course_id, seeds, email="boris@example.com", name="Борис",
                level=0, block_answer=None, is_correct=lambda n: (n + 1) % 3 != 0, lessons=1,
            )
    finally:
        app.state.accounts.close()
    print(f"Курс {course_id}. Аня: {ann} ответов, Борис: {boris} ответов. Данные — {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Демо-ученики для показа (без ключа модели).")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "showcase")
    parser.add_argument("--course", default="trees-demo")
    args = parser.parse_args()
    asyncio.run(main(args.out, args.course))
