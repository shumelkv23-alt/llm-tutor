"""Показ приложения: каждое действие ядра — наглядно в терминале.

Читает те же файлы, что и сервер (пути — из ``.env`` или переменных окружения
``ACCOUNTS_DB_PATH``/``USERS_DIR``), и считает всё функциями самого приложения:
контекст — ``core.context.build_context``, оценку — ``student.beta.estimate``,
режим — ``student.planner``, задание «Объясни новичку» — ``student.explain``.
Модель не вызывается.

    uv run python scripts/showcase.py students
    uv run python scripts/showcase.py watch                  # живая лента: что пишет каждый ход
    uv run python scripts/showcase.py context                # что видит тьютор
    uv run python scripts/showcase.py context --role novice  # новичок / evaluator / mentor / project
    uv run python scripts/showcase.py mastery
    uv run python scripts/showcase.py review
    uv run python scripts/showcase.py explain [--reference grouping]
    uv run python scripts/showcase.py project
    uv run python scripts/showcase.py time-travel --days 3   # сдвинуть сроки повторения
    uv run python scripts/showcase.py close-module 1 2       # «пройти» модули (только для показа)

Ученик — ``--email`` (если учеников один — не нужен), курс — ``--course``
(по умолчанию демо-курс ``trees-demo``).
Команды, которые пишут в БД (``time-travel``, ``close-module``), — только для показа.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.db.connection import get_conn
from llm_tutor.student import beta, planner
from llm_tutor.student import explain as explain_logic
from llm_tutor.student import review as review_logic

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400.0

# --- оформление -----------------------------------------------------------

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
          "blue": "34", "magenta": "35", "cyan": "36"}


def c(text: object, *styles: str) -> str:
    if not USE_COLOR or not styles:
        return str(text)
    return f"\033[{';'.join(_CODES[s] for s in styles)}m{text}\033[0m"


def header(title: str, hint: str = "") -> None:
    print()
    print(c(f"━━ {title} ", "bold", "cyan") + c("━" * max(0, 70 - len(title)), "cyan"))
    if hint:
        print(c(hint, "dim"))


def bar(value: float, width: int = 20) -> str:
    value = max(0.0, min(1.0, value))
    full = round(value * width)
    color = "green" if value >= 0.75 else "yellow" if value >= 0.5 else "red"
    return c("█" * full, color) + c("░" * (width - full), "dim")


def _width(text: str) -> int:
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", text))


def table(headers: list[str], rows: list[list[object]]) -> None:
    cells = [[str(x) for x in row] for row in rows]
    widths = [max([_width(h)] + [_width(row[i]) for row in cells]) for i, h in enumerate(headers)]
    line = "  ".join(c(h.ljust(w), "bold") for h, w in zip(headers, widths))
    print(line)
    print(c("  ".join("─" * w for w in widths), "dim"))
    for row in cells:
        print("  ".join(x + " " * (w - _width(x)) for x, w in zip(row, widths)))


def when(ts: float | None, now: float) -> str:
    if ts is None:
        return "—"
    days = (ts - now) / DAY
    stamp = datetime.fromtimestamp(ts, UTC).strftime("%d.%m %H:%M")
    if days >= 0:
        return f"{stamp} UTC (через {days:.1f} дн)"
    return c(f"{stamp} UTC (просрочено {-days:.1f} дн)", "yellow")


# --- где лежат данные -----------------------------------------------------


def settings() -> Settings:
    """Настройки сервера; без ключа модели (он здесь не нужен) — заглушка."""
    try:
        return Settings()
    except ValidationError:
        return Settings(openrouter_api_key="showcase")


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def students(s: Settings) -> list[sqlite3.Row]:
    path = _path(s.accounts_db_path)
    if not path.exists():
        sys.exit(f"Нет базы аккаунтов {path}. Запусти сервер и зарегистрируй ученика.")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT id, email, name, is_admin FROM users ORDER BY id").fetchall()
    finally:
        conn.close()


def student_db(s: Settings, email: str | None, course: str) -> tuple[sqlite3.Row, Path]:
    users = students(s)
    with_db = [u for u in users if (_path(s.users_dir) / str(u["id"]) / f"{course}.sqlite3").exists()]
    if email:
        chosen = [u for u in users if u["email"] == email.lower()]
        if not chosen:
            sys.exit(f"Нет ученика {email}. Список: showcase.py students")
    elif len(with_db) == 1:
        chosen = with_db
    else:
        sys.exit("Учеников несколько — укажи --email (список: showcase.py students)")
    user = chosen[0]
    path = _path(s.users_dir) / str(user["id"]) / f"{course}.sqlite3"
    if not path.exists():
        sys.exit(f"У {user['email']} нет базы курса {course}: запишись на курс в браузере.")
    return user, path


def open_db(path: Path) -> sqlite3.Connection:
    return get_conn(str(path), check_same_thread=False)


def lesson_state(conn: sqlite3.Connection):
    session_id = repos.get_open_session(conn)
    return session_id, (repos.get_session_state(conn, session_id) if session_id else None)


def full_graph(conn: sqlite3.Connection) -> CourseGraph:
    return CourseGraph.load(conn)


def name_of(graph: CourseGraph, node_id: str | None) -> str:
    if node_id is None:
        return "—"
    return graph.concept(node_id).name if graph.has_node(node_id) else node_id


# --- students -------------------------------------------------------------


def cmd_students(s: Settings, args) -> None:
    header("Ученики", f"аккаунты: {_path(s.accounts_db_path)} · базы: {_path(s.users_dir)}/<id>/<курс>.sqlite3")
    rows = []
    for user in students(s):
        path = _path(s.users_dir) / str(user["id"]) / f"{args.course}.sqlite3"
        if path.exists():
            conn = open_db(path)
            events = conn.execute("SELECT count(*) FROM events").fetchone()[0]
            _, state = lesson_state(conn)
            closed = sum(1 for st in (state.route.steps if state and state.route else []) if st.status == "closed")
            conn.close()
            info = [events, closed, path.relative_to(ROOT) if path.is_relative_to(ROOT) else path]
        else:
            info = ["—", "—", c("не записан на курс", "dim")]
        rows.append([user["id"], user["email"], user["name"], "да" if user["is_admin"] else "", *info])
    table(["id", "email", "имя", "админ", "events", "закрыто", "файл базы"], rows)


# --- context --------------------------------------------------------------

_BLOCK_TITLES = (
    ("Ты — тьютор", "Кто ты и модуль"), ("Правила", "Правила материала"), ("Материалов", "Правила материала"),
    ("Как говорить", "Как говорить"), ("Как вести диалог", "Как вести диалог"),
    ("Лестница подсказок", "Лестница подсказок"), ("Как ведёшь занятие", "Как вести занятие"),
    ("Маршрут", "Маршрут"), ("Фаза занятия", "Фаза"), ("Сейчас ты на уровне", "Уровень подсказки"),
    ("Ответь строго JSON", "Контракт JSON"), ("Самооценка ученика", "Профиль (анкета)"),
    ("Состояние занятия", "Состояние занятия"), ("Модель ученика", "Срез модели ученика"),
    ("Оглавление занятия", "Оглавление занятия"), ("Конспект текущего раздела", "Конспект раздела"),
)


def _blocks(system: str) -> list[tuple[str, str]]:
    """Режет системный промпт тьютора на блоки по известным заголовкам."""
    blocks: list[tuple[str, str]] = []
    for chunk in system.split("\n\n"):
        title = next((t for start, t in _BLOCK_TITLES if chunk.lstrip().startswith(start)), None)
        if title is None and blocks:
            blocks[-1] = (blocks[-1][0], blocks[-1][1] + "\n\n" + chunk)
        else:
            blocks.append((title or "Прочее", chunk))
    return blocks


def _show_messages(messages, *, full: bool, lines: int, split_system: bool = False) -> None:
    total = sum(len(m.content) for m in messages)
    print(c(f"Сообщений: {len(messages)} · знаков: {total} · ≈ {total // 4} токенов", "dim"))
    role_color = {"system": "magenta", "user": "blue", "assistant": "green"}
    for index, message in enumerate(messages):
        parts = _blocks(message.content) if split_system and message.role == "system" else [("", message.content)]
        print()
        print(c(f"[{index}] {message.role}", "bold", role_color.get(message.role, "cyan"))
              + c(f"  {len(message.content)} зн.", "dim"))
        for title, text in parts:
            if title:
                share = len(text) / max(total, 1)
                print(f"  {c('▸ ' + title, 'bold')}  {bar(share * 4, 12)} {c(f'{len(text)} зн.', 'dim')}")
            body = text.splitlines()
            shown = body if full else body[:lines]
            for line in shown:
                print(c("  │ ", "dim") + line)
            if len(body) > len(shown):
                print(c(f"  │ … ещё {len(body) - len(shown)} строк (--full)", "dim"))


def cmd_context(s: Settings, args) -> None:
    _, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    session_id, state = lesson_state(conn)
    if session_id is None:
        sys.exit("У ученика нет сессии: пройди анкету в браузере.")
    graph = full_graph(conn)
    if args.role == "tutor":
        from llm_tutor.core.context import build_context

        package = build_context(
            conn, session_id, args.text, state=state, graph=graph,
            top_k=s.context_rag_top_k, dialog_tail=s.context_dialog_tail,
            budget_tokens=s.context_budget_tokens, settings=s,
        )
        header("Контекст тьютора на этот ход",
               f"занятие: {name_of(graph, state.current_node_id)} · реплика: «{args.text}» · "
               f"материалы RAG: {package.material_state}, фрагментов {len(package.chunks)}")
        print(c("Собирается заново из БД на каждый ход (core/context.build_context). Модель не вызывалась.", "dim"))
        _show_messages(package.messages, full=args.full, lines=args.lines, split_system=True)
        return
    if args.role == "project":
        from llm_tutor.core import project as core_project
        from llm_tutor.llm.prompt_config import load_project

        project = repos.current_project(conn)
        if project is None:
            sys.exit("У ученика нет проекта: выбери описание на странице «Проект».")
        closed, mentioned = core_project._lessons(conn, project)
        tail = repos.get_project_turns(conn, project.id)[-load_project(_path(s.prompts_dir)).limits.tail_turns:]
        messages = core_project.mentor_messages(
            load_project(_path(s.prompts_dir)), project, course_title=_course_title(args.course),
            closed_lessons=closed, brief_lessons=mentioned, tail=tail, text=args.text,
        )
        header("Контекст наставника проекта", f"проект: {project.brief.title} · статус {project.status}")
        _show_messages(messages, full=args.full, lines=args.lines)
        return
    # роли «Объясни новичку»
    from llm_tutor.core import explain as core_explain
    from llm_tutor.llm.prompt_config import load_explain

    prompts = load_explain(_path(s.prompts_dir))
    session = repos.active_explain_session(conn)
    node_id = args.node or (session.concept_id if session else None) or explain_logic.pending_lesson(
        conn, state.route, graph.topo_order())
    if node_id is None:
        sys.exit("Нет занятия для «Объясни новичку»: закрой важное занятие или укажи --node.")
    lesson = core_explain._lesson(conn, node_id)
    turns = repos.get_explain_turns(conn, session.id) if session else []
    tail = turns[-prompts.limits.tail_turns:]
    explain_state = session.state if session else None
    if args.role == "novice":
        plan = explain_logic.TurnPlan("ask", lesson=node_id)
        directive = core_explain.directive_text(
            prompts, plan, title=lesson.title, topic=core_explain._ask_topic(lesson, len(turns)))
        messages = core_explain.novice_messages(
            prompts, title=lesson.title, known=lesson.known, tail=tail, student_text=args.text, directive=directive)
        leaked = any(idea.text in m.content for m in messages for idea in lesson.config.ideas)
        header("Что видит новичок", f"занятие: {lesson.title}")
        print(c("Эталона в сообщениях новичка нет: " + ("ОШИБКА, найден!" if leaked else "проверено ✓"),
                "red" if leaked else "green"))
    elif args.role == "evaluator":
        from llm_tutor.schemas import ExplainState

        messages = core_explain.evaluator_messages(
            prompts, lesson.config, explain_state or ExplainState(), tail=tail, text=args.text, speaker="student")
        header("Что видит скрытый оценщик", f"занятие: {lesson.title} · модель {s.explain_evaluator_model}")
        print(c("Эталон в сообщениях оценщика: есть ✓ (по нему он и судит)", "green"))
    else:
        messages = core_explain.mentor_messages(
            prompts, lesson.config, theory=lesson.theory[0] if lesson.theory else None, tail=tail, text=args.text)
        header("Что видит наставник «Объясни новичку»", f"занятие: {lesson.title}")
    _show_messages(messages, full=args.full, lines=args.lines)


def _course_title(course_id: str) -> str:
    from llm_tutor.web import courses

    course = courses.get_course(course_id)
    return course.title if course is not None else course_id


# --- mastery --------------------------------------------------------------


def cmd_mastery(s: Settings, args) -> None:
    _, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    _, state = lesson_state(conn)
    graph = full_graph(conn)
    now = time.time()
    steps = {st.concept_id: st for st in (state.route.steps if state and state.route else [])}
    rows_db = {r["concept_id"]: r for r in conn.execute("SELECT * FROM mastery")}
    sched = repos.get_review_rows(conn)
    header("Модель ученика: оценка владения по занятиям",
           "mean = α/(α+β) · σ — ширина Beta · забывание к Beta(1,1): x' = 1 + (x−1)·e^(−λ·дни) · режим — planner")
    print(c(f"λ = {s.beta_decay_lambda}/сут · пороги: skip ≥ {s.mastery_skip_threshold} и σ ≤ "
            f"{planner.CONFIDENT_UNCERTAINTY} · verify ≥ {s.mastery_verify_threshold} · compressed ≥ "
            f"{s.mastery_compressed_threshold}", "dim"))
    status_mark = {"closed": c("✔ закрыто", "green"), "current": c("▶ сейчас", "yellow"),
                   "claimed": c("🔍 знакомое", "magenta"), "ahead": c("· впереди", "dim")}
    rows = []
    for node_id in graph.topo_order():
        st = steps.get(node_id)
        if not args.all and node_id not in rows_db and (st is None or st.status == "ahead"):
            continue
        m = beta.estimate(conn, node_id, now=now, settings=s)
        raw = rows_db.get(node_id)
        stored = f"{raw['alpha']:.2f}/{raw['beta']:.2f}" if raw else "1/1"
        decayed = f"{m.alpha:.2f}/{m.beta:.2f}"
        mode = planner.mode_for_node(conn, graph, node_id, now=now, settings=s)
        row = sched.get(node_id)
        rows.append([
            f"М{graph.topic_of(node_id)}", c(node_id, "dim"), name_of(graph, node_id)[:34],
            status_mark.get(st.status, st.status) + (c(" ⚠️", "yellow") if st and st.weak else "") if st else "—",
            stored, decayed, f"{bar(m.mean)} {m.mean:.2f}", f"{m.uncertainty:.3f}",
            planner.MODE_LABELS.get(mode, mode), f"ступень {row.step}" if row else "",
        ])
    table(["", "id", "занятие", "статус", "α/β в БД", "α/β сейчас", "оценка", "σ", "режим", "повтор"], rows)
    print(c("\n«в БД» — как записал последний ход; «сейчас» — с забыванием на этот момент.", "dim"))
    if args.node:
        _mastery_history(conn, graph, args.node, s)


def _mastery_history(conn, graph, node_id: str, s: Settings) -> None:
    """Пересчёт α/β занятия по журналу: каждое событие и что оно добавило."""
    header(f"История оценки «{name_of(graph, node_id)}»",
           "пересчёт из events: α += вес·result, β += вес·(1−result), между событиями — забывание")
    alpha, beta_ = s.beta_prior_alpha, s.beta_prior_beta
    last = None
    rows = []
    for e in conn.execute("SELECT * FROM events WHERE concept_id = ? ORDER BY ts, id", (node_id,)):
        if last is not None:
            alpha, beta_ = beta.decay(alpha, beta_, (e["ts"] - last) / DAY, s.beta_decay_lambda)
        alpha += e["weight"] * e["result"]
        beta_ += e["weight"] * (1 - e["result"])
        last = e["ts"]
        mean = alpha / (alpha + beta_)
        verdict = c("верно", "green") if e["result"] >= 0.5 else c("неверно", "red")
        rows.append([e["id"], datetime.fromtimestamp(e["ts"], UTC).strftime("%d.%m %H:%M"),
                     e["source"], e["item_id"] or "", verdict, f"{e['weight']:.2f}",
                     f"{alpha:.2f}/{beta_:.2f}", f"{bar(mean, 14)} {mean:.2f}"])
    table(["event", "время UTC", "источник", "задание", "ответ", "вес", "α/β", "оценка"], rows)
    print(c("Распространение (+0.15·вес к α пререквизитов успешного узла) пишется в mastery, но не в events: "
            "у пререквизитов «в БД» может быть больше, чем даёт их журнал.", "dim"))


# --- review ---------------------------------------------------------------


def cmd_review(s: Settings, args) -> None:
    _, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    _, state = lesson_state(conn)
    graph = full_graph(conn)
    now = time.time()
    header("Расписание повторений (review_schedule)",
           "лестница 1 → 3 → 7 → 14 → 30 → 60 дней; верно в срок — ступень +1, ошибка — −2 и завтра")
    print(c(f"Повторение {'включено' if review_logic.enabled(conn) else 'ВЫКЛЮЧЕНО'} (факт review_enabled)", "dim"))
    rows = []
    for node_id, row in sorted(repos.get_review_rows(conn).items(), key=lambda kv: kv[1].due_at):
        ladder = "".join(c("●", "green") if i < row.step else c("◉", "yellow") if i == row.step else c("○", "dim")
                         for i in range(len(review_logic.LADDER_DAYS)))
        rows.append([name_of(graph, node_id)[:36], ladder, row.step,
                     f"{review_logic.LADDER_DAYS[row.step]:.0f} дн", when(row.due_at, now)])
    if rows:
        table(["занятие", "лестница", "ступень", "интервал", "срок"], rows)
    else:
        print("Строк нет: ни одно занятие ещё не закрыто.")
    due = review_logic.due_lessons(conn, graph, state.route if state else None, now=now, settings=s)
    header("Что попадёт в повторение прямо сейчас", "due_lessons: закрыто · в графе · срок наступил · есть задание")
    if not due:
        print("Ничего: сроки не наступили (сдвинь их: showcase.py time-travel --days 3).")
    for i, node_id in enumerate(due, 1):
        tag = []
        if i <= review_logic.WARMUP_SIZE:
            tag.append("разминка")
        if i <= review_logic.SERIES_SIZE:
            tag.append("«Срочное»")
        print(f"  {i}. {name_of(graph, node_id)}  {c(' · '.join(tag), 'dim')}")


def cmd_time_travel(s: Settings, args) -> None:
    _, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    shift = args.days * DAY
    where, params = ("WHERE concept_id = ?", (shift, args.node)) if args.node else ("", (shift,))
    changed = conn.execute(f"UPDATE review_schedule SET due_at = due_at - ? {where}", params).rowcount
    conn.commit()
    print(c(f"⏩ Сроки повторения сдвинуты на {args.days} дн назад: строк {changed}. "
            "Обнови страницу — на главной появится «к повторению».", "yellow"))
    cmd_review(s, args)


# --- explain --------------------------------------------------------------

_STATE_MARK = {"correct": c("✔ correct", "green"), "partial": c("◐ partial", "yellow"),
               "wrong": c("✘ wrong", "red"), "missing": c("○ missing", "dim")}


def cmd_explain(s: Settings, args) -> None:
    _, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    _, state = lesson_state(conn)
    graph = full_graph(conn)
    configs = repos.get_explain_configs(conn)
    if args.reference:
        config = configs.get(args.reference)
        if config is None:
            sys.exit(f"У занятия {args.reference} нет эталона. Важные: {', '.join(configs)}")
        header(f"Эталон «{name_of(graph, args.reference)}» — его видят оценщик и наставник, но не новичок")
        table(["идея", "обязат.", "текст", "что ждём"],
              [[i.id, "да" if i.required else "", i.text, i.expect] for i in config.ideas])
        print()
        table(["загвоздка", "новичок скажет", "верно"], [[p.id, p.belief, p.fix] for p in config.pitfalls])
        return
    header("«Объясни новичку»: задания", f"важных занятий с эталоном: {len(configs)}")
    done = repos.finished_required(conn)
    closed = {st.concept_id: st.closed_at for st in (state.route.steps if state and state.route else [])
              if st.status == "closed"}
    since = float(repos.get_fact(conn, explain_logic.SINCE_FACT) or "inf")
    pending = explain_logic.pending_lesson(conn, state.route if state else None, graph.topo_order())
    rows = []
    for node_id in graph.topo_order():
        if node_id not in configs:
            continue
        if node_id == pending:
            mark = c("▶ висит задание — курс ждёт объяснения", "yellow")
        elif node_id in done:
            mark = c("✔ объяснено", "green")
        elif node_id in closed and (closed[node_id] or 0) >= since:
            mark = c("ждёт очереди (задания идут по одному)", "dim")
        elif node_id in closed:
            mark = c("закрыто до обновления — задания нет", "dim")
        else:
            mark = c("· не закрыто", "dim")
        rows.append([f"М{graph.topic_of(node_id)}", name_of(graph, node_id), mark])
    table(["", "важное занятие", "задание"], rows)
    sessions = repos.list_explain_sessions(conn)
    if not sessions:
        print(c("\nСессий ещё нет.", "dim"))
        return
    header("Сессии")
    table(["id", "занятие", "тип", "статус", "конец", "реплик", "покрытие обязательных"],
          [[x.id, name_of(graph, x.concept_id), x.kind, x.status, x.end_reason or "",
            x.state.student_turns,
            f"{x.report.required_done}/{x.report.required_total}" if x.report else ""] for x in sessions])
    session = next((x for x in sessions if x.id == args.session), None) if args.session else (
        repos.active_explain_session(conn) or sessions[0])
    config = configs.get(session.concept_id or "")
    header(f"Сессия {session.id}: «{name_of(graph, session.concept_id)}»",
           "покрытие считает код по вердиктам оценщика; correct без дословной цитаты → partial")
    if config:
        rows = []
        for idea in config.ideas:
            progress = session.state.ideas.get(idea.id)
            rows.append([idea.id, "да" if idea.required else "",
                         _STATE_MARK[progress.state if progress else "missing"],
                         c("наставник", "magenta") if progress and progress.mentor else "",
                         (progress.quote or "")[:60] if progress else ""])
        table(["идея", "обязат.", "состояние", "помощь", "цитата ученика"], rows)
    header("Реплики", "meta.directive — установка, которую код дал новичку на этот ход")
    who = {"student": c("Ученик", "blue"), "novice": c("🐣 Новичок", "green"), "mentor": c("🧑‍🏫 Наставник", "magenta")}
    for t in repos.get_explain_turns(conn, session.id):
        extra = ""
        if t.meta.get("directive"):
            extra = c(f"  [установка: {t.meta['directive']}]", "yellow")
        if t.meta.get("mentor"):
            extra = c("  [вопрос наставнику]", "magenta")
        print(f"{who.get(t.role, t.role)}: {t.content[:140]}{extra}")
    events = conn.execute(
        "SELECT id, concept_id, result, weight, citation FROM events WHERE source = 'explain' ORDER BY id").fetchall()
    if events:
        header("События журнала от объяснений (source = explain)", "correct — 0.3, correct с наставником — 0.15, wrong — 0.3")
        table(["event", "занятие", "result", "вес", "основание"],
              [[e["id"], name_of(graph, e["concept_id"]), e["result"], e["weight"], e["citation"][:60]] for e in events])


# --- project --------------------------------------------------------------


def cmd_project(s: Settings, args) -> None:
    from llm_tutor.core.overview import course_progress
    from llm_tutor.student import project as project_logic
    from llm_tutor.web import courses

    course = courses.get_course(args.course)
    if course is not None and courses.project_file(course) is None:
        header("Проект по курсу")
        print(c(f"У курса {args.course} нет project.json — страница «Проект» его не показывает.", "yellow"))
        print("Проект есть у курса, в каталоге модулей которого лежит project.json (mlcourse, trees-demo).")
        return
    _, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    progress = course_progress(conn, settings=s)
    header("Проект по курсу", "открывается, когда закрыты все занятия опубликованных модулей")
    print(f"Курс пройден: {progress.closed} из {progress.total} — "
          + (c("проект можно выбрать", "green") if progress.total and progress.closed == progress.total
             else c("на странице «Проект» замок", "yellow")))
    rows = conn.execute("SELECT id, brief_id, status, started_at, done_at FROM projects ORDER BY id").fetchall()
    if not rows:
        print(c("Проектов ещё нет.", "dim"))
        return
    table(["id", "описание", "статус", "начат"],
          [[r["id"], r["brief_id"], r["status"], datetime.fromtimestamp(r["started_at"], UTC).strftime("%d.%m %H:%M")]
           for r in rows])
    project = repos.current_project(conn) or repos.get_project(conn, rows[-1]["id"])
    template = project_logic.TEMPLATES[project.template]
    header(f"Карточка: «{project.brief.title}» · статус {project.status}",
           "карточку меняет только ученик; «Утвердить идею» — когда все поля по правилам")
    missing = set(project_logic.card_missing(template, project.state.card))
    for field in template.card:
        value = project.state.card.get(field.key, "")
        mark = c("✘", "red") if field.key in missing else c("✔", "green")
        print(f"  {mark} {c(field.label, 'bold')}\n      {value or c('(пусто)', 'dim')}")
    header("Этапы", "«Этап выполнен» — когда обязательные заметки не короче 10 знаков")
    for stage in template.stages:
        notes = project.state.notes.get(stage.id, {})
        if stage.id in project.state.done_stages:
            mark = c("✔ выполнен", "green")
        elif stage.id == project.state.stage:
            mark = c("▶ текущий", "yellow")
        else:
            mark = c("· впереди", "dim")
        need = project_logic.notes_missing(stage, notes)
        print(f"  {mark}  {c(stage.title, 'bold')} — {stage.goal}")
        for field in stage.fields:
            value = notes.get(field.key, "")
            flag = c("*", "red") if field.required else " "
            state_ = c("не заполнено", "red") if field.key in need else (value[:70] or c("—", "dim"))
            print(f"        {flag} {field.label[:60]}: {state_}")
    header("Чат с наставником", "meta.proposal — предложение полей карточки (уже очищенное кодом)")
    for t in repos.get_project_turns(conn, project.id):
        who = c("Ученик", "blue") if t.role == "student" else c("Наставник", "magenta")
        extra = c(f"  [предложение: {', '.join(t.meta['proposal'])}]", "yellow") if t.meta.get("proposal") else ""
        print(f"{who}: {t.content[:160].replace(chr(10), ' ')}{extra}")


# --- watch ----------------------------------------------------------------


def _snapshot(conn: sqlite3.Connection) -> dict:
    _, state = lesson_state(conn)
    keys = ("current_node_id", "section_id", "mode", "phase", "hint_level", "pending_item_id",
            "check_misses", "passed_sections", "weak_sections", "final_passed", "lesson_part", "task_hinted")
    st = {k: getattr(state, k) for k in keys} if state else {}
    if state is not None:
        review = state.review
        st["review"] = f"{review.kind}: {review.node_id} ({review.answered} отвечено)" if review else None
    steps = {x.concept_id: x.status for x in (state.route.steps if state and state.route else [])}
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "event_id": one("SELECT coalesce(max(id), 0) FROM events"),
        "message_id": one("SELECT coalesce(max(id), 0) FROM messages"),
        "explain_turn_id": one("SELECT coalesce(max(id), 0) FROM explain_turns"),
        "project_turn_id": one("SELECT coalesce(max(id), 0) FROM project_turns"),
        "mastery": {r[0]: (r[1], r[2]) for r in conn.execute("SELECT concept_id, alpha, beta FROM mastery")},
        "review": {k: (v.step, v.due_at) for k, v in repos.get_review_rows(conn).items()},
        "state": st,
        "steps": steps,
        "explain": {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, status, state FROM explain_sessions")},
        "projects": {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, status, state FROM projects")},
    }


def _print_changes(conn, graph, old: dict, new: dict) -> None:
    stamp = c(datetime.now().astimezone().strftime("%H:%M:%S"), "dim")
    out: list[str] = []
    for m in conn.execute("SELECT id, role, content, meta FROM messages WHERE id > ? ORDER BY id", (old["message_id"],)):
        who = c("ученик ", "blue") if m["role"] == "user" else c("тьютор ", "green")
        meta = c(f"  meta={m['meta']}", "dim") if m["meta"] else ""
        out.append(f"💬 {who}{m['content'][:90].replace(chr(10), ' ')}{meta}")
    for e in conn.execute("SELECT * FROM events WHERE id > ? ORDER BY id", (old["event_id"],)):
        verdict = c("верно", "green") if e["result"] >= 0.5 else c("неверно", "red")
        out.append(f"📥 {c('events', 'bold')} #{e['id']} {name_of(graph, e['concept_id'])} · {e['source']} · "
                   f"задание {e['item_id'] or '—'} · {verdict} (result {e['result']}) · вес {e['weight']:.2f}")
    for cid, (a, b) in new["mastery"].items():
        before = old["mastery"].get(cid)
        if before == (a, b):
            continue
        a0, b0 = before or (1.0, 1.0)
        m0, m1 = a0 / (a0 + b0), a / (a + b)
        arrow = c("▲", "green") if m1 > m0 else c("▼", "red") if m1 < m0 else "="
        out.append(f"📈 {c('mastery', 'bold')} {name_of(graph, cid)}: α {a0:.2f}→{a:.2f}, β {b0:.2f}→{b:.2f}, "
                   f"оценка {m0:.2f} {arrow} {m1:.2f} {bar(m1, 12)}")
    if old["state"].get("current_node_id") is None and new["state"].get("current_node_id"):
        out.append(f"🧭 {c('state', 'bold')} создано: занятие {new['state'].get('current_node_id')}, "
                   f"раздел {new['state'].get('section_id')}, фаза {new['state'].get('phase')}")
    for key, value in new["state"].items():
        if old["state"].get("current_node_id") is None:
            break
        if old["state"].get(key) != value:
            out.append(f"🧭 {c('state', 'bold')}.{key}: {old['state'].get(key)!r} → {c(repr(value), 'yellow')}")
    for cid, status in new["steps"].items():
        if old["steps"].get(cid) != status and old["steps"]:
            out.append(f"🗺  {c('маршрут', 'bold')} {name_of(graph, cid)}: {old['steps'].get(cid)} → {c(status, 'yellow')}")
    for cid in set(old["review"]) | set(new["review"]):
        a, b = old["review"].get(cid), new["review"].get(cid)
        if a != b:
            text = "удалена (закреплено)" if b is None else f"ступень {a[0] if a else '—'} → {b[0]}, срок {when(b[1], time.time())}"
            out.append(f"🔁 {c('review_schedule', 'bold')} {name_of(graph, cid)}: {text}")
    for t in conn.execute("SELECT * FROM explain_turns WHERE id > ? ORDER BY id", (old["explain_turn_id"],)):
        meta = json.loads(t["meta"]) if t["meta"] else {}
        extra = c(f"  [установка {meta['directive']}]", "yellow") if meta.get("directive") else ""
        out.append(f"🐣 {c('explain', 'bold')} {t['role']}: {t['content'][:90].replace(chr(10), ' ')}{extra}")
    for sid, (status, raw) in new["explain"].items():
        before = old["explain"].get(sid)
        if before == (status, raw):
            continue
        ideas = json.loads(raw).get("ideas", {})
        coverage = ", ".join(f"{k}={v.get('state')}" for k, v in ideas.items()) or "—"
        out.append(f"🐣 {c('explain_sessions', 'bold')} #{sid}: {status} · покрытие: {coverage}")
    for t in conn.execute("SELECT * FROM project_turns WHERE id > ? ORDER BY id", (old["project_turn_id"],)):
        meta = json.loads(t["meta"]) if t["meta"] else {}
        extra = c(f"  [предложение: {', '.join(meta['proposal'])}]", "yellow") if meta.get("proposal") else ""
        out.append(f"🛠  {c('project', 'bold')} {t['role']}: {t['content'][:90].replace(chr(10), ' ')}{extra}")
    for pid, (status, raw) in new["projects"].items():
        if old["projects"].get(pid) != (status, raw):
            st = json.loads(raw)
            out.append(f"🛠  {c('projects', 'bold')} #{pid}: {status} · этап {st.get('stage')} · "
                       f"выполнены {st.get('done_stages')} · карточка: {', '.join(st.get('card', {})) or '—'}")
    if out:
        print(c("─" * 72, "dim"))
        for line in out:
            print(f"{stamp} {line}")


def cmd_watch(s: Settings, args) -> None:
    user, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    graph = full_graph(conn)
    header(f"Живая лента: {user['email']}", f"{path} · раз в {args.interval} с · Ctrl+C — выход")
    print(c("Делай ходы в браузере — здесь появится всё, что ход записал в базу, одним коммитом.", "dim"))
    old = _snapshot(conn)
    try:
        while True:
            time.sleep(args.interval)
            conn.rollback()  # свежий снимок WAL
            new = _snapshot(conn)
            if new != old:
                _print_changes(conn, graph, old, new)
                old = new
    except KeyboardInterrupt:
        print()


# --- close-module ---------------------------------------------------------


def cmd_close_module(s: Settings, args) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import dev_close_module

    _, path = student_db(s, args.email, args.course)
    conn = open_db(path)
    for topic in args.modules:
        closed = dev_close_module.close_module(conn, topic)
        print(c(f"модуль {topic}: закрыто занятий — {closed} (без событий в журнале, только для показа)", "yellow"))
    cmd_explain(s, argparse.Namespace(**{**vars(args), "reference": None, "session": None}))


# --- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Показ llm_tutor: каждое действие ядра наглядно.")
    parser.add_argument("--email", help="ученик (по умолчанию — единственный записанный)")
    parser.add_argument("--course", default="trees-demo", help="id курса (по умолчанию демо-курс)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("students", help="ученики и их базы")
    p = sub.add_parser("context", help="из чего собирается контекст модели")
    p.add_argument("--role", default="tutor", choices=["tutor", "novice", "evaluator", "mentor", "project"])
    p.add_argument("--text", default="Не понял, объясни подробнее.")
    p.add_argument("--node", help="занятие для ролей «Объясни новичку»")
    p.add_argument("--full", action="store_true", help="показать блоки целиком")
    p.add_argument("--lines", type=int, default=6, help="строк на блок без --full")
    p = sub.add_parser("mastery", help="оценка владения, режимы, статусы")
    p.add_argument("--all", action="store_true", help="все 33 занятия")
    p.add_argument("--node", help="история α/β занятия по журналу")
    sub.add_parser("review", help="расписание повторений и что повторять сейчас")
    p = sub.add_parser("time-travel", help="сдвинуть сроки повторения назад")
    p.add_argument("--days", type=float, default=3.0)
    p.add_argument("--node")
    p = sub.add_parser("explain", help="задания и сессии «Объясни новичку»")
    p.add_argument("--reference", metavar="NODE", help="показать эталон занятия")
    p.add_argument("--session", type=int)
    sub.add_parser("project", help="проект: карточка, этапы, чат")
    p = sub.add_parser("watch", help="живая лента записей в базу")
    p.add_argument("--interval", type=float, default=1.0)
    p = sub.add_parser("close-module", help="закрыть модули (только для показа)")
    p.add_argument("modules", type=int, nargs="+")
    args = parser.parse_args(argv)
    handlers = {
        "students": cmd_students, "context": cmd_context, "mastery": cmd_mastery, "review": cmd_review,
        "time-travel": cmd_time_travel, "explain": cmd_explain, "project": cmd_project,
        "watch": cmd_watch, "close-module": cmd_close_module,
    }
    handlers[args.command](settings(), args)


if __name__ == "__main__":
    main()
