"""Пройти весь курс за ученика — для подготовки аккаунта к показу.

Ходит через настоящее API приложения (тот же код, что у браузера), пишет в
настоящие базы из ``.env`` (``ACCOUNTS_DB_PATH``/``USERS_DIR``), но вместо модели
стоит заглушка: ключ не нужен, денег и минут не тратится. Поэтому в журнале
``events`` остаются настоящие свидетельства, закрытия занятий, расписание
повторений и сессии «Объясни новичку» — в отличие от ``showcase.py close-module``,
который закрывает занятия без событий.

    uv run python scripts/pass_course.py --email ann@example.com --password …
    uv run python scripts/pass_course.py --email ann@example.com --password … --course python-mastery
    uv run python scripts/pass_course.py --email new@example.com --password … --register --name Аня
    uv run python scripts/pass_course.py --email ann@example.com --password … --wrong-every 4

Как отвечает «ученик»:

- анкета каждого модуля — уровень и самооценка ``--survey-answer`` (по умолчанию
  «Знаю в теории»; «Уверенно» не берём: тогда занятия идут проверочным проходом);
- выбор и короткий ответ — верный ответ из seed;
- код — результат проверки «пройдено» (в браузере его присылает Pyodide);
- открытый и голосовой ответ — текст критериев рубрики, заглушка засчитывает их
  по дословной цитате, как и настоящая проверка цитат;
- «Объясни новичку» — обязательные идеи эталона занятия, затем «Продолжить»;
- разминки повторения на границе занятий проходятся по пути.

``--wrong-every N`` — каждый N-й ответ неверный: прогресс выглядит живым, у
занятий появляются слабые места. Только для показа: на рабочем ученике так можно
«пройти» курс, ничего не решив.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

from llm_tutor.config import Settings
from llm_tutor.grader.rubric import CriterionVerdict, RubricVerdict
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict, NoviceReply, ProjectMentorReply
from llm_tutor.web import courses
from llm_tutor.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
LESSON_TEXT = "Суть шага.\n\n---\n\nПример.\n\n---\n\nГде это применяют."
MAX_TURNS = 5000
# Эталон занятия в сообщении оценщику (core/explain.evaluator_messages).
_REFERENCE = re.compile(r"Эталон занятия:\n(\{.*?\n\})", re.DOTALL)


class StubLLM:
    """Модель-заглушка: урок — готовый текст, рубрика засчитывает ожидаемый ответ.

    Перед ответом на задание с рубрикой скрипт кладёт в ``criteria`` её критерии:
    вердикт «пройдено» с цитатой — сам текст критерия, который и стоит в ответе.
    Оценщик «Объясни новичку» засчитывает идею, если её текст есть в реплике.
    """

    def __init__(self) -> None:
        self.criteria: list[dict] = []

    async def chat_structured(self, messages, schema, *, model=None, temperature=None):
        if schema is RubricVerdict:
            return RubricVerdict(criteria=[
                CriterionVerdict(id=c["id"], passed=True, quote=c["criterion"]) for c in self.criteria
            ])
        if schema is ExplainObservation:
            text = messages[-1].content
            match = _REFERENCE.search(text)
            ideas = json.loads(match.group(1))["ideas"] if match else []
            said = text.rsplit("Реплика ", 1)[-1]
            return ExplainObservation(ideas=[
                IdeaVerdict(id=idea["id"], verdict="correct", quote=idea["text"])
                for idea in ideas
                if idea["text"] in said
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


def _seeds(course_id: str) -> list[dict]:
    course = courses.get_course(course_id)
    if course is None:
        sys.exit(f"Нет курса {course_id}.")
    folder = course.root or course.data_dir or ROOT / "data"
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("seed_topic*.json"))]


def _fail(response: httpx.Response, what: str) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = response.text[:300]
    sys.exit(f"{what}: HTTP {response.status_code} — {detail}")


async def _sign_in(client: httpx.AsyncClient, args) -> None:
    if args.register:
        response = await client.post(
            "/api/auth/register",
            json={"email": args.email, "name": args.name or args.email.split("@")[0], "password": args.password},
        )
        _fail(response, "Регистрация")
        return
    response = await client.post("/api/auth/login", json={"email": args.email, "password": args.password})
    _fail(response, "Вход (проверьте почту и пароль; новый аккаунт — с --register)")


async def _enroll(client: httpx.AsyncClient, course_id: str) -> None:
    listing = (await client.get("/api/courses")).json()["courses"]
    card = next((card for card in listing if card["id"] == course_id), None)
    if card is not None and card.get("enrolled"):
        return
    _fail(await client.post(f"/api/courses/{course_id}/enroll", json={}), f"Запись на курс {course_id}")


async def _survey(client: httpx.AsyncClient, lesson: str, answer: int) -> None:
    """Анкета модуля по шагам, как в браузере: уровень и самооценка по блокам."""
    answers: list[int] = []
    for _ in range(50):
        response = await client.post(f"{lesson}/survey/next", json={"answers": answers})
        _fail(response, "Анкета")
        question = response.json().get("question")
        if not question:
            break
        options = question.get("options") or []
        answers.append(min(answer, len(options) - 1) if options else answer)
    _fail(await client.post(f"{lesson}/survey/finish", json={"answers": answers}), "Анкета (итог)")


async def pass_course(client: httpx.AsyncClient, stub: StubLLM, course_id: str, args) -> dict:
    seeds = _seeds(course_id)
    items = {item["id"]: item for seed in seeds for item in seed["items"]}
    criteria: dict[int, list[dict]] = {}
    for seed in seeds:
        for criterion in seed.get("criteria", []):
            criteria.setdefault(criterion["rubric_id"], []).append(criterion)
    reference = {node["id"]: node["explain"] for seed in seeds for node in seed["nodes"] if node.get("explain")}
    lesson = f"/api/courses/{course_id}/lesson"
    stats = {"answers": 0, "wrong": 0, "explained": 0, "surveys": 0}
    last_closed = -1
    same = 0
    previous = None

    for _ in range(MAX_TURNS):
        state = (await client.get(f"{lesson}/state")).json()["state"]
        if state.get("survey"):
            await _survey(client, lesson, args.survey_answer)
            stats["surveys"] += 1
            continue

        explain = state.get("explain") or {}
        if explain.get("blocked"):
            active = explain.get("active")
            if active and active["status"] == "active":
                ideas = reference[active["node_id"]]["ideas"]
                text = " ".join(idea["text"] for idea in ideas if idea["required"])
                _fail(await client.post(f"{lesson}/explain/turn", json={"text": text}), "Объясни новичку")
            else:
                _fail(await client.post(f"{lesson}/explain/continue", json={}), "Объясни новичку (Продолжить)")
                stats["explained"] += 1
            continue

        total = len(state.get("topics") or [])
        closed = state.get("closed", 0)
        if closed != last_closed:
            print(f"  закрыто занятий: {closed} из {total}", flush=True)
            last_closed = closed
        # Блок review в состоянии есть всегда; идущая серия — review.active.
        if total and closed >= total and not (state.get("review") or {}).get("active"):
            return {**stats, "closed": closed, "total": total}

        # Защита от зацикливания: состояние не меняется много ходов подряд.
        fingerprint = json.dumps([state.get("item"), state.get("lesson_button"), closed], sort_keys=True, default=str)
        same = same + 1 if fingerprint == previous else 0
        previous = fingerprint
        if same > 20:
            sys.exit(f"Курс не двигается: занятие {state.get('node', {}) and state['node'].get('id')}, "
                     f"закрыто {closed} из {total}.")

        item = state.get("item")
        if item:
            seed = items[item["id"]]
            stats["answers"] += 1
            wrong = bool(args.wrong_every) and stats["answers"] % args.wrong_every == 0
            stats["wrong"] += wrong
            if item.get("runnable"):
                body = {"item_id": item["id"], "code": "result = 1", "passed": not wrong,
                        "report": "AssertionError" if wrong else ""}
                response = await client.post(f"{lesson}/code-result", json=body)
            else:
                kind = seed["answer_type"]
                stub.criteria = [] if wrong else criteria.get(seed.get("rubric_id"), [])
                if kind == "choice":
                    right = int(seed["answer"])
                    answer = (right + 1) % len(item["options"]) if wrong else right
                elif kind in ("open", "spoken"):
                    answer = "Не знаю." if wrong else " ".join(c["criterion"] for c in stub.criteria) or "Ответ."
                else:
                    answer = "не знаю" if wrong else str(seed.get("answer", ""))
                response = await client.post(f"{lesson}/answer", json={"item_id": item["id"], "answer": answer})
                stub.criteria = []
            _fail(response, f"Ответ на задание {item['id']}")
        elif state.get("lesson_button"):
            button = state["lesson_button"]
            response = await client.post(f"{lesson}/next", json={"node_id": button["node_id"], "part": button["part"]})
            _fail(response, "Дальше")
        else:
            _fail(await client.post(f"{lesson}/task", json={}), "Взять задание")
    sys.exit("Слишком много ходов — курс так и не закрылся.")


async def main(args) -> None:
    settings = Settings()  # .env и переменные окружения — те же базы, что у сервера
    app = create_app(settings, client=(stub := StubLLM()), mailer=NullMailer())
    print(f"Базы: {settings.accounts_db_path}, {settings.users_dir}")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            await _sign_in(client, args)
            await _enroll(client, args.course)
            print(f"{args.email}: прохожу курс {args.course}…")
            result = await pass_course(client, stub, args.course, args)
    finally:
        app.state.accounts.close()
    print(
        f"Готово: закрыто {result['closed']} из {result['total']} занятий, ответов {result['answers']} "
        f"(неверных {result['wrong']}), объяснений новичку {result['explained']}, анкет {result['surveys']}."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Пройти весь курс за ученика (для показа, без модели).")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--course", default="trees-demo", help="id курса (по умолчанию демо-курс)")
    parser.add_argument("--register", action="store_true", help="создать аккаунт, если его ещё нет")
    parser.add_argument("--name", default=None, help="имя для --register")
    parser.add_argument("--wrong-every", type=int, default=0, help="каждый N-й ответ неверный (0 — все верные)")
    parser.add_argument("--survey-answer", type=int, default=1,
                        help="ответ анкеты: 0 «Впервые вижу», 1 «Знаю в теории», 2 «С подсказками»")
    asyncio.run(main(parser.parse_args()))
