"""«Объясни новичку»: логика без модели (срез 86).

Оценщик (LLM) только сообщает, что увидел в реплике (``ExplainObservation``);
покрытие идей, выбор хода новичка, отчёт, события и расписание считает этот
модуль. Функции чистые: БД не трогают, модель не зовут.
"""

import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from llm_tutor.db import repos
from llm_tutor.llm.prompt_config import ExplainLimits
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict
from llm_tutor.schemas import (
    EndReason,
    Event,
    ExplainConfig,
    ExplainPitfall,
    ExplainReport,
    ExplainState,
    IdeaProgress,
    IdeaReport,
    PitfallReport,
    PitfallUse,
    ReviewRow,
    Route,
)
from llm_tutor.student import review
from llm_tutor.student.review import ScheduleChange

# Шингл детектора вставки — столько слов подряд.
SHINGLE_WORDS = 8
# Столько символов противоречия получает новичок.
CONTRADICTION_MAX_CHARS = 300
# Столько ходов без изменения покрытия — повод предложить наставника.
STALL_TURNS = 3
# Со второй просьбы «дай ответ» новичок предлагает наставника.
ANSWER_REQUESTS_TO_MENTOR = 2
# Идея, объяснённая после помощи наставника, весит столько от обычного.
MENTOR_WEIGHT_SHARE = 0.5
_DAY = 86_400.0
# Факт: с какого момента закрытое важное занятие даёт обязательное объяснение (срез 93).
SINCE_FACT = "explain_since"

Directive = Literal["own_words", "confused", "offer_mentor", "wrap_up", "pitfall", "no_answer", "ask"]


@dataclass(frozen=True)
class TurnPlan:
    """Что новичок делает на этом ходу. ``lesson`` — id занятия, не текст идеи."""

    directive: Directive
    lesson: str | None = None
    pitfall: ExplainPitfall | None = None
    contradiction: str | None = None


# Разметка и кавычки, которые не меняют слов: **жирный**, `код`, «ёлочки», "лапки".
_MARKUP = str.maketrans("", "", "`*«»“”„\"'")


def normalize(text: str) -> str:
    """Текст для сверки цитат и шинглов: без регистра, ё, разметки, кавычек и лишних пробелов."""
    lowered = text.lower().replace("ё", "е").translate(_MARKUP)
    return re.sub(r"\s+", " ", lowered).strip()


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = normalize(text).split()
    return {tuple(words[i : i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def looks_pasted(text: str, theory: Sequence[str], *, min_chars: int, overlap: float) -> bool:
    """Длинная реплика, почти дословно взятая из конспектов модуля."""
    if len(text) < min_chars:
        return False
    own = _shingles(text)
    if not own:
        return False
    known: set[tuple[str, ...]] = set()
    for page in theory:
        known |= _shingles(page)
    return len(own & known) / len(own) >= overlap


def clean_observation(
    obs: ExplainObservation,
    config: ExplainConfig,
    student_text: str,
    *,
    context: Sequence[str] = (),
) -> ExplainObservation:
    """Наблюдение после проверки кодом: модель не может приписать ученику чужих слов.

    Неизвестные id отброшены. Цитата, которой нет в реплике дословно, снимается:
    «correct» без неё — «partial», «wrong» без неё не засчитывается вовсе.
    Противоречие остаётся, только если это слова ученика — из этой реплики или
    прошлых (``context``): иначе через него к новичку утёк бы эталон.
    """
    idea_ids = {idea.id for idea in config.ideas}
    pitfall_ids = {pitfall.id for pitfall in config.pitfalls}
    text = normalize(student_text)
    ideas = []
    for verdict in obs.ideas:
        if verdict.id not in idea_ids:
            continue
        quote = normalize(verdict.quote or "")
        if quote and quote in text:
            ideas.append(verdict)
        elif verdict.verdict == "correct":
            ideas.append(IdeaVerdict(id=verdict.id, verdict="partial"))
        elif verdict.verdict == "partial":
            ideas.append(IdeaVerdict(id=verdict.id, verdict="partial"))
    contradiction = (obs.contradiction or "").strip()[:CONTRADICTION_MAX_CHARS] or None
    if contradiction is not None:
        said = [text, *(normalize(line) for line in context)]
        if not any(normalize(contradiction) in line for line in said):
            contradiction = None
    return obs.model_copy(
        update={
            "ideas": ideas,
            "pitfalls_named": [p for p in obs.pitfalls_named if p in pitfall_ids],
            "contradiction": contradiction,
        }
    )


def _next_state(old: str, verdict: str) -> str:
    """Таблица переходов спеки: верное засчитывается всегда, неверное — последнее слово."""
    if verdict == "correct":
        return "correct"
    if verdict == "wrong":
        return "wrong"
    return "partial" if old == "missing" else old


def apply_observation(state: ExplainState, obs: ExplainObservation, *, pasted: bool) -> ExplainState:
    """Покрытие и счётчики после реплики ученика новичку."""
    turn = state.student_turns + 1
    ideas = dict(state.ideas)
    if not pasted:
        for verdict in obs.ideas:
            old = ideas.get(verdict.id, IdeaProgress())
            new_state = _next_state(old.state, verdict.verdict)
            if new_state == old.state:
                continue
            ideas[verdict.id] = IdeaProgress(
                state=new_state,
                quote=verdict.quote,
                mentor=new_state == "correct" and verdict.id in state.mentor_ideas,
            )
    used = [
        use.model_copy(update={"fixed": obs.pitfall_fixed})
        if use.turn == state.student_turns and use.fixed is None and obs.pitfall_fixed is not None
        else use
        for use in state.pitfalls_used
    ]
    named = list(state.pitfalls_named)
    named += [p for p in dict.fromkeys(obs.pitfalls_named) if p not in named]
    return state.model_copy(
        update={
            "ideas": ideas,
            "pitfalls_used": used,
            "pitfalls_named": named,
            "student_turns": turn,
            "pasted_turns": state.pasted_turns + int(pasted),
            "answer_requests": state.answer_requests + int(obs.asks_for_answer),
            "stalled_turns": 0 if ideas != state.ideas else state.stalled_turns + 1,
        }
    )


def required_progress(state: ExplainState, config: ExplainConfig) -> tuple[int, int]:
    """Обязательные идеи: сколько объяснено верно и сколько всего."""
    required = [idea.id for idea in config.ideas if idea.required]
    done = sum(1 for idea in required if state.ideas.get(idea, IdeaProgress()).state == "correct")
    return done, len(required)


def _pitfall_candidate(state: ExplainState, config: ExplainConfig) -> ExplainPitfall | None:
    """Неиспользованная загвоздка по занятию, о котором ученик уже говорил."""
    spoken = {
        idea.lesson
        for idea in config.ideas
        if state.ideas.get(idea.id, IdeaProgress()).state in ("partial", "correct")
    }
    taken = {use.id for use in state.pitfalls_used} | set(state.pitfalls_named)
    for pitfall in config.pitfalls:
        if pitfall.id not in taken and pitfall.lesson in spoken:
            return pitfall
    return None


def _ask_lesson(state: ExplainState, config: ExplainConfig, lesson_order: Sequence[str]) -> str | None:
    """Первое занятие, по идеям которого ещё ничего не сказано (или сказано не всё)."""
    for wanted in ("missing", "partial"):
        for lesson in lesson_order:
            if any(
                idea.lesson == lesson
                and state.ideas.get(idea.id, IdeaProgress()).state == wanted
                for idea in config.ideas
            ):
                return lesson
    return lesson_order[0] if lesson_order else None


def choose_directive(
    state: ExplainState,
    obs: ExplainObservation,
    config: ExplainConfig,
    *,
    pasted: bool,
    limits: ExplainLimits,
    lesson_order: Sequence[str],
) -> tuple[TurnPlan, ExplainState]:
    """Установка хода новичка — первая подходящая по порядку спеки.

    ``state`` — уже после ``apply_observation``. Возвращает и состояние: загвоздка
    и «кажется, понял» отмечаются в нём, чтобы не повторяться.
    """
    if pasted:
        return TurnPlan("own_words"), state
    if obs.contradiction:
        return TurnPlan("confused", contradiction=obs.contradiction), state
    stalled = state.stalled_turns >= STALL_TURNS
    if (
        obs.frustrated
        or (obs.asks_for_answer and state.answer_requests >= ANSWER_REQUESTS_TO_MENTOR)
        or stalled
    ):
        # Счёт застоя — заново: иначе наставника предлагали бы каждым ходом.
        return TurnPlan("offer_mentor"), state.model_copy(update={"stalled_turns": 0})
    done, total = required_progress(state, config)
    if not state.wrap_up_offered and total and done / total >= limits.wrap_up_share:
        return TurnPlan("wrap_up"), state.model_copy(update={"wrap_up_offered": True})
    since = state.student_turns - (state.last_pitfall_turn or 0)
    if since >= limits.pitfall_every and len(state.pitfalls_used) < limits.pitfall_max:
        pitfall = _pitfall_candidate(state, config)
        if pitfall is not None:
            use = PitfallUse(id=pitfall.id, turn=state.student_turns)
            return TurnPlan("pitfall", lesson=pitfall.lesson, pitfall=pitfall), state.model_copy(
                update={
                    "pitfalls_used": [*state.pitfalls_used, use],
                    "last_pitfall_turn": state.student_turns,
                }
            )
    if obs.asks_for_answer:
        return TurnPlan("no_answer"), state
    return TurnPlan("ask", lesson=_ask_lesson(state, config, lesson_order)), state


# --- Итог сессии: отчёт, свидетельства, повторения ---


def build_report(
    state: ExplainState, config: ExplainConfig, *, topic_id: int, end_reason: EndReason
) -> ExplainReport:
    """Отчёт по эталону; после сессии эталон открывается ученику.

    Идеи, которых больше нет в эталоне, в отчёт не попадают; новые — ``missing``.
    """
    ideas = []
    for idea in config.ideas:
        progress = state.ideas.get(idea.id, IdeaProgress())
        ideas.append(
            IdeaReport(
                id=idea.id,
                lesson=idea.lesson,
                state=progress.state,
                quote=progress.quote,
                mentor=progress.mentor,
                reference=None if progress.state == "correct" else idea.text,
            )
        )
    used = {use.id: use for use in state.pitfalls_used}
    pitfalls = []
    for pitfall in config.pitfalls:
        if pitfall.id in state.pitfalls_named:
            outcome = "named"
        elif pitfall.id in used:
            fixed = used[pitfall.id].fixed
            # None — ученик не ответил ясно (или оценщик не ответил): это не
            # «пропустил ошибку», в повторение такое не идёт.
            outcome = "fixed" if fixed else "missed" if fixed is False else "unclear"
        else:
            continue
        pitfalls.append(
            PitfallReport(
                id=pitfall.id,
                lesson=pitfall.lesson,
                outcome=outcome,
                fix=pitfall.fix if outcome in ("missed", "unclear") else None,
            )
        )
    required = {idea.id for idea in config.ideas if idea.required}
    done, total = required_progress(state, config)
    wrong, gaps = _review_split(ideas, pitfalls, required)
    return ExplainReport(
        topic_id=topic_id,
        required_done=done,
        required_total=total,
        ideas_done=sum(1 for idea in ideas if idea.state == "correct"),
        ideas_total=len(ideas),
        ideas=ideas,
        pitfalls=pitfalls,
        review_lessons=[*wrong, *gaps],
        student_turns=state.student_turns,
        mentor_used=state.mentor_turns > 0,
        pasted_turns=state.pasted_turns,
        end_reason=end_reason,
    )


def _review_split(
    ideas: Sequence[IdeaReport], pitfalls: Sequence[PitfallReport], required: set[str]
) -> tuple[list[str], list[str]]:
    """Занятия «повторить»: с ошибками и (отдельно, без них) с пробелами — по порядку эталона."""
    wrong = list(dict.fromkeys(idea.lesson for idea in ideas if idea.state == "wrong"))
    gap_lessons = [
        idea.lesson
        for idea in ideas
        if idea.id in required and idea.state in ("missing", "partial")
    ] + [pitfall.lesson for pitfall in pitfalls if pitfall.outcome == "missed"]
    gaps = [lesson for lesson in dict.fromkeys(gap_lessons) if lesson not in wrong]
    return wrong, gaps


def plan_evidence(report: ExplainReport, *, weight: float, now: float) -> list[Event]:
    """События журнала: по одному на верную или неверную идею.

    ``partial`` и ``missing`` событий не дают: отсутствие объяснения — не
    свидетельство незнания. Помощь наставника вдвое снижает вес верной идеи.
    """
    events = []
    for idea in report.ideas:
        if idea.state not in ("correct", "wrong"):
            continue
        correct = idea.state == "correct"
        events.append(
            Event(
                source="explain",
                result=1.0 if correct else 0.0,
                concept_id=idea.lesson,
                weight=weight * MENTOR_WEIGHT_SHARE if correct and idea.mentor else weight,
                citation=f"{idea.id}: {idea.quote or ''}".strip(),
                ts=now,
            )
        )
    return events


def plan_schedule(
    report: ExplainReport,
    config: ExplainConfig,
    rows: Mapping[str, ReviewRow],
    *,
    now: float,
) -> list[ScheduleChange]:
    """Изменения лестницы повторений: не больше одного на занятие.

    Ошибка — как неверный ответ в повторении (две ступени вниз, завтра); пробел —
    завтра на той же ступени; нет строки — ступень 0 на завтра. Всё верно —
    расписание не трогается.
    """
    required = {idea.id for idea in config.ideas if idea.required}
    wrong, gaps = _review_split(report.ideas, report.pitfalls, required)
    changes = []
    for lesson in wrong:
        change = review.plan_answer(
            rows.get(lesson), lesson, passed=False, hinted=False, due=False, now=now
        )
        if change is not None:
            changes.append(change)
    for lesson in gaps:
        row = rows.get(lesson)
        changes.append(ScheduleChange(lesson, row.step if row else 0, now + _DAY))
    return changes


# --- Висящее обязательное объяснение (срез 93) ---


def mark_since(conn: sqlite3.Connection, now: float) -> None:
    """Пишет ``explain_since`` один раз: занятия, закрытые раньше, заданий не дают."""
    if repos.get_fact(conn, SINCE_FACT) is None:
        repos.set_fact(conn, SINCE_FACT, str(now), source="system")


def pending_lesson(
    conn: sqlite3.Connection, route: Route | None, order: Sequence[str]
) -> str | None:
    """Самое раннее по ``order`` важное занятие, которое ученик ещё не объяснил.

    Задание не хранится флагом, а вычисляется: занятие закрыто в снимке маршрута
    не раньше ``explain_since``, у него есть эталон и нет завершённой обязательной
    сессии. Так оно появляется при любом пути закрытия и не теряется при сбое хода.
    """
    since_text = repos.get_fact(conn, SINCE_FACT)
    if route is None or since_text is None:
        return None
    try:
        since = float(since_text)
    except ValueError:
        return None
    closed = {
        step.concept_id: step.closed_at
        for step in route.steps
        if step.status == "closed" and step.closed_at is not None and step.closed_at >= since
    }
    if not closed:
        return None
    important = repos.get_explain_configs(conn).keys() & closed.keys()
    done = repos.finished_required(conn)
    return next((node for node in order if node in important and node not in done), None)
