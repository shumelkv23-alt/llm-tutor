"""Обработчик хода: ответ тьютора или разбор ответа на задание (Срез 5.5).

Один ход = один коммит: реплики, события, модель ученика и состояние сессии
ложатся вместе — сбой посередине не оставит «сиротскую» реплику или событие
без обновления владения.

Разветвление: если в состоянии сессии стоит ``pending_item_id``, текст ученика
— это ответ на задание (проверяет код, без LLM); иначе идёт тьюторский путь с
лестницей подсказок.
"""

import logging
import sqlite3
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.core import intents, lesson
from llm_tutor.core.context import build_context
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.grader import autocheck, rubric
from llm_tutor.llm.client import LLMClient, LLMError
from llm_tutor.llm.prompts import EMPTY_GRAPH_REPLY, LLM_FAILURE_REPLY
from llm_tutor.llm.schemas import TutorReply
from llm_tutor.schemas import Event, Item, Route, SessionState
from llm_tutor.student import beta, diagnostic, guide, hints, survey
from llm_tutor.student import route as route_mod

logger = logging.getLogger(__name__)

# Задание уже неактуально (пропало из банка или не проверяется кодом).
STALE_ITEM_REPLY = "Это задание уже неактуально — жми /task, чтобы взять новое."
# Реплика «дай задание» попадает в журнал как обычная просьба ученика.
PRACTICE_KICKOFF_TEXT = "Давай задание по текущей теме."
# Напоминание, что вопрос не сбросил выданное задание.
PENDING_ITEM_NOTE = (
    "Задание всё ещё ждёт ответа — ответь вариантом или пришли /skip."
)
# Повод тьюторского хода при нажатии «Не понимаю».
STUCK_KICKOFF_TEXT = "Не понял, давай подробнее по текущей теме."
SKIP_KICKOFF_TEXT = "Пропустить задание."
SKIP_REPLY = (
    "Пропустил — свидетельство не записано, вернёмся к этому узлу позже. "
    "Напиши что-нибудь — дам следующее задание."
)
NOTHING_TO_SKIP_REPLY = "Сейчас нет задания, которое нужно пропустить."
GRADING_FAILED_REPLY = (
    "Это задание не удалось проверить — снимаю его. Возьми новое: /task."
)
# По текущему узлу заданий в банке нет — ведём диалогом, узел не рвём.
NO_TASK_FOR_NODE_REPLY = (
    "По этому узлу новых заданий сейчас нет — свежие ты уже отвечал. "
    "Возьми ещё раз то же: /task, или спрашивай — объясню."
)
# Узел закрыт — объявляем следующий шаг (имя подставит вызывающий код).
STUCK_NOTE = "Ок, остаёмся на этом узле и разбираемся глубже."
# Курс пройден: открытых тем не осталось ни в одном модуле (§4.3 спеки модулей).
ROUTE_DONE_REPLY = "🎓 Курс пройден — открытых тем не осталось. Свериться можно в /plan."
# Модуль пройден: все темы его рабочего участка закрыты.
TOPIC_DONE_NOTE = route_mod.TOPIC_DONE_NOTE
# Переход в следующий модуль: с какой темы начинаем.
NEXT_TOPIC_NOTE = "Дальше — модуль {number} «{title}», начинаем с «{name}»."
# Закрыта последняя открытая тема курса.
COURSE_DONE_NOTE = "🎓 Это был последний шаг — курс пройден! Свериться можно в /plan."
# Повтор уже закрытой темы, когда курс пройден: «🎓» второй раз не нужен.
COURSE_STILL_DONE_NOTE = "Открытых тем по-прежнему нет — свериться можно в /plan."
# Следующий модуль ещё без анкеты: урок продолжится после неё (§5.2 спеки модулей).
NEXT_TOPIC_SURVEY_NOTE = (
    "Дальше — модуль {number} «{title}». Сначала пара вопросов — подберу, с чего начать."
)
# Повод хода «Продолжить обучение» — в журнале виден как реплика ученика.
RESUME_KICKOFF_TEXT = "Продолжаем занятие."
# Шапка шага проверочного прохода. Номер без общего числа: после неудачного
# ответа серия обнуляется и шагов может стать больше двух.
VERIFY_STEP_TEMPLATE = "🔎 Проверка «{name}» — шаг {step}"
# Рубричные задания просим объяснить словами — это и есть суть проверки.
VERIFY_EXPLAIN_PREFIX = "Объясни своими словами:"
# Проход исчерпал задания узла, а серия ещё не набрана.
VERIFY_EXHAUSTED_REPLY = (
    "Проверить нечем: по этой теме мало заданий, и мы их уже прошли. "
    "Разберём её уроком — напиши что угодно, и продолжим."
)
# У узла нет ни одного задания.
VERIFY_NO_ITEMS_REPLY = (
    "По этой теме у меня нет проверочных заданий — "
    "закрою её, когда владение подтвердится по ходу."
)
# Проход не подтвердился — говорим честно и возвращаемся к разбору.
VERIFY_FAILED_NOTE = "Пока не подтвердилось — вернёмся к теме и разберёмся."
# Вход в заявленный в анкете узел: самооценку подтверждаем проходом (срез 23).
CLAIMED_CHECK_NOTE = "🔍 Проверим знакомое: «{name}» — пара быстрых вопросов."


@dataclass(frozen=True)
class TurnReply:
    """Ответ хода: текст и, если задание вынесено отдельно, ``tail``.

    ``tail`` — второе сообщение хода (обычно задание): объяснение и тест в
    одном сообщении читаются стеной. ``options`` — подписи кнопок того
    сообщения, которое несёт задание: ``tail``, если он есть, иначе ``text``.
    """

    text: str
    options: list[str] | None = None
    tail: str | None = None
    # Кнопка урока (срез 28): подпись и callback_data «Дальше» / «Проверим».
    button: tuple[str, str] | None = None


def _render_item(item: Item) -> str:
    """Задание как текст сообщения (варианты — нумерованным списком)."""
    if not item.options:
        return item.prompt
    options = "\n".join(
        f"{index + 1}. {option}" for index, option in enumerate(item.options)
    )
    return f"{item.prompt}\n\n{options}"


def _normalize_choice_answer(item: Item, text: str) -> str:
    """Номер варианта из списка (нумерация с 1) превращает в текст варианта.

    Задание показывается нумерованным списком, а ``autocheck`` ждёт либо текст
    варианта, либо 0-based индекс — набранная цифра без этой поправки
    засчитала бы верный ответ неверным.
    """
    stripped = text.strip()
    if item.answer_type != "choice" or not stripped.isdigit() or stripped in item.options:
        # Вариант-число («150») — это текст варианта, а не номер (F-1).
        return text
    number = int(stripped)
    if 1 <= number <= len(item.options):
        return item.options[number - 1]
    return text


def _feedback(item: Item, score: float) -> str:
    """Разбор ответа: вердикт и — при ошибке — верный вариант."""
    if score >= diagnostic.SUCCESS_SCORE:
        return "Верно ✓"
    if item.answer_type == "choice":
        return f"Не совсем ✗ — верный вариант: {item.options[int(item.answer or 0)]}"
    if item.answer is None:
        # У рубричного задания эталона нет — показывать нечего (разбор даст урок).
        return "Не совсем ✗"
    return f"Не совсем ✗ — ожидался ответ: {item.answer}"


def post_turn(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    user_text: str,
    assistant_text: str,
    state: SessionState,
    events: list[Event] | None = None,
    mastery: list[beta.MasteryUpdate] | None = None,
    now: float | None = None,
) -> None:
    """Пишет ход одним коммитом; при сбое откатывает всё."""
    stamp = time.time() if now is None else now
    try:
        repos.add_message(conn, session_id, "user", user_text, ts=stamp, commit=False)
        repos.add_message(
            conn, session_id, "assistant", assistant_text, ts=stamp, commit=False
        )
        for event in events or []:
            repos.add_event(conn, event, stamp, commit=False)
        for change in mastery or []:
            beta.write_update(conn, change, commit=False)
        repos.update_session_state(conn, session_id, state, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


async def _answer_branch(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    graph: CourseGraph,
    user_text: str,
    state: SessionState,
    *,
    now: float,
    settings: Settings,
) -> tuple[str, list[Event], list[beta.MasteryUpdate], SessionState, bool | None]:
    """Ученик отвечает на выданное задание.

    ``choice``/``short`` проверяет код, ``open``/``code`` — рубричный грейдер
    (его вердикт идёт в журнал с ограниченным весом). Пятый элемент — был ли
    ответ верным (``False``) или верным (``True``): закрывать узел на
    неудачном ответе нельзя. ``None`` — задание проверено не было (пропало из
    банка или его нечем проверить): это технический сбой, а не ошибка ученика.
    """
    item = (
        repos.get_item(conn, state.pending_item_id)
        if state.pending_item_id is not None
        else None
    )
    if item is None:
        return (
            STALE_ITEM_REPLY,
            [],
            [],
            state.model_copy(update={"pending_item_id": None}),
            None,
        )

    try:
        if item.answer_type in diagnostic.AUTO_CHECKABLE:
            result = autocheck.check(item, _normalize_choice_answer(item, user_text))
        elif item.answer_type in diagnostic.RUBRIC_CHECKABLE and item.rubric_id is not None:
            result = await rubric.grade(
                conn, client, model, item, user_text, settings=settings
            )
        else:
            # Тип задания код проверять не умеет (например, открытый ответ без
            # рубрики) — это сбой задания, а не ошибка ученика.
            return (
                STALE_ITEM_REPLY,
                [],
                [],
                state.model_copy(update={"pending_item_id": None}),
                None,
            )
    except (rubric.RubricError, autocheck.AutoCheckError):
        # Задание нельзя проверить (рубрику убрали, эталон битый) — снимаем его,
        # иначе оно залипнет и ученик не сможет выйти из него иначе как /skip.
        logger.warning("Задание %s не проверяется, снимаю", item.id)
        return (
            GRADING_FAILED_REPLY,
            [],
            [],
            state.model_copy(update={"pending_item_id": None}),
            None,
        )

    measured = state.current_node_id or next(iter(item.concept_weights), "")
    # Повтор того же вопроса — свидетельство слабее: знания могло и не прибавиться.
    first_time = not repos.item_was_answered(conn, item.id)
    evidence_scale = 1.0 if first_time else settings.repeat_evidence_weight
    events, mastery = diagnostic.plan_evidence(
        conn,
        graph,
        item,
        measured,
        result.score,
        source="checked" if item.answer_type in diagnostic.AUTO_CHECKABLE else "rubric",
        weight_scale=evidence_scale
        * (
            1.0
            if item.answer_type in diagnostic.AUTO_CHECKABLE
            else settings.rubric_evidence_weight
        ),
        now=now,
        settings=settings,
    )
    new_state = state.model_copy(
        update={
            "pending_item_id": None,
            "attempts": state.attempts + 1,
            "last_activity": now,
        }
    )
    # Помощь по этому заданию — факт, а не выбранный моделью уровень: спросил
    # или попросил глубины, значит ответ не «без подсказок».
    #
    # Чистота нужна проверочному проходу: там ученик утверждает, что тему знает,
    # и помощь обесценивает доказательство. В уроке бот объясняет первым по своей
    # же схеме (§5.2), поэтому просьба о помощи перестала быть признаком «списал»
    # — серию считают верные ответы.
    hinted = (state.task_hinted or state.hint_level > 0) and state.mode == "verify"
    passed = result.score >= diagnostic.SUCCESS_SCORE
    new_state = guide.register_answer(
        new_state, correct=passed, hinted=hinted, settings=settings
    )
    return _feedback(item, result.score), events, mastery, new_state, passed


async def _tutor_branch(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    session_id: int,
    user_text: str,
    state: SessionState,
    graph: CourseGraph,
    *,
    now: float,
    settings: Settings,
    force_stuck: bool = False,
    allow_stuck: bool = True,
) -> tuple[str, list[Event], list[beta.MasteryUpdate], SessionState, bool]:
    """Тьюторский путь: контекст → модель → новый уровень подсказки.

    Пятый элемент — просьба закрыть тему из флага модели: она обрабатывается
    вызывающим кодом (проход живёт в ``core/verify.py``).

    ``allow_stuck=False`` — ход, на котором узел выбирает код (вход в узел,
    объяснение следующего): ученик этого узла ещё не видел, и «застрял» от
    модели относится не к нему, а к прежнему разговору.
    """
    package = build_context(
        conn,
        session_id,
        user_text,
        state=state,
        graph=graph,
        top_k=settings.context_rag_top_k,
        dialog_tail=settings.context_dialog_tail,
        budget_tokens=settings.context_budget_tokens,
        now=now,
        settings=settings,
    )
    idle_state = state.model_copy(update={"last_activity": now})

    try:
        answer = await client.chat_structured(package.messages, TutorReply, model=model)
    except LLMError:
        return LLM_FAILURE_REPLY, [], [], idle_state, False
    except Exception:  # noqa: BLE001 — бот не должен молчать на неожиданный сбой
        logger.exception("Неожиданный сбой тьюторского хода")
        return LLM_FAILURE_REPLY, [], [], idle_state, False

    level = hints.next_hint_level(state.hint_level, answer.hint_level)
    new_state = state.model_copy(update={"hint_level": level, "last_activity": now})
    if state.pending_item_id is not None:
        # Пока задание висело, ученик получил помощь: ответ уже не «без подсказок»,
        # даже если модель на этом ходу опустила уровень до нуля.
        new_state = new_state.model_copy(update={"task_hinted": True})
    if (answer.student_stuck and allow_stuck) or force_stuck:
        # Ученик просит глубины (или написал «не понял»): узел в усиленный
        # проход, вперёд не идём. Лестницу поднимаем ровно на одну ступень от
        # уровня ДО хода: `new_state` уже содержит подъём от модели, и второй
        # подъём поверх него дал бы +2 за ход.
        stuck_state = guide.on_student_stuck(new_state).model_copy(
            update={
                "hint_level": hints.next_hint_level(
                    state.hint_level, state.hint_level + 1
                ),
                "task_hinted": True,
            }
        )
        return f"{answer.reply}\n\n{STUCK_NOTE}", [], [], stuck_state, False
    return answer.reply, [], [], new_state, answer.wants_close_topic


def reset_lesson(
    conn: sqlite3.Connection, *, claimed: Collection[str] = (), now: float | None = None
) -> None:
    """Урок с чистого листа после анкеты модуля: снимает тему, задание и режим.

    Снимок маршрута НЕ обнуляется (срез 26): в нём закрытое прошлых модулей.
    Текущая тема снова «впереди», а ``claimed`` — знакомое по только что
    пройденной анкете — помечается заявленным (``route.mark_claimed``). Так
    /task до анкеты не отменяет «Уверенно» (срез 23). Свидетельства в журнале
    не трогаем — владение остаётся.
    """
    stamp = time.time() if now is None else now
    session_id = repos.get_open_session(conn)
    if session_id is None:
        return
    state = repos.get_session_state(conn, session_id)
    route = (
        route_mod.mark_claimed(route_mod.release_current(state.route), claimed)
        if state.route is not None
        else None
    )
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(
            update={
                "current_node_id": None,
                "mode": None,
                "hint_level": 0,
                "pending_item_id": None,
                "route": route,
                "phase": "explain",
                "node_streak": 0,
                "task_hinted": False,
                "verify_item_ids": [],
                "lesson_item_ids": [],
                "last_activity": stamp,
                "jump_node_id": None,
            }
        ),
    )


def _is_claimed(route: Route | None, node_id: str) -> bool:
    """Заявлен ли узел в анкете и ещё не подтверждён (по снимку маршрута)."""
    return route is not None and any(
        step.concept_id == node_id and step.status == "claimed" for step in route.steps
    )


def _fresh_route(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    state: SessionState,
    *,
    now: float,
    settings: Settings,
) -> Route:
    """Снимок, достроенный до всего курса перед выбором темы.

    Сохранённый снимок мог отстать от курса (снимок до среза 25, модуль
    добавлен после «🎓»): решение по нему объявило бы конец курса, когда
    в новом модуле есть темы (аудит среза 25, H1).
    """
    return route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=state.current_node_id,
        previous=state.route,
        now=now,
        settings=settings,
    )


def _topic_title(conn: sqlite3.Connection, topic_id: int) -> str:
    """Название модуля для объявлений (без модуля в БД — его номер)."""
    topic = repos.get_topic(conn, topic_id)
    return topic.title if topic is not None else f"№{topic_id}"


def _checking(state: SessionState, node_id: str) -> SessionState:
    """Состояние входа в проверочный проход по заявленному узлу."""
    return state.model_copy(
        update={
            "current_node_id": node_id,
            "mode": "verify",
            "close_requested": False,
            "node_streak": 0,
            "hint_level": 0,
            "task_hinted": False,
            "pending_item_id": None,
            "phase": "explain",
            "verify_item_ids": [],
            "lesson_item_ids": [],
        }
    )


def _enter_claimed(
    conn: sqlite3.Connection,
    session_id: int,
    graph: CourseGraph,
    state: SessionState,
    route: Route,
    node_id: str,
    *,
    user_text: str,
    now: float,
    settings: Settings,
) -> TurnReply:
    """Ход входа в заявленный узел: объявление и первое задание прохода.

    Модель не зовём: ученик сказал, что тему знает, — объяснять нечего, нужно
    подтвердить. Провал прохода сам уведёт узел в обычный урок.
    """
    checking = _checking(state.model_copy(update={"route": route}), node_id)
    new_state, task_text, options = _issue_task(
        conn, graph, checking, now=now, settings=settings
    )
    note = CLAIMED_CHECK_NOTE.format(name=graph.concept(node_id).name)
    tail = task_text if new_state.pending_item_id is not None else None
    text = note if tail else f"{note}\n\n{task_text}"
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=now, settings=settings)
    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=f"{text}\n\n{tail}" if tail else text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=now,
    )
    return TurnReply(text=text, options=options if tail else None, tail=tail)


async def handle_turn(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    user_text: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
    allow_intents: bool = True,
) -> TurnReply:
    """Один ход диалога: ответ на задание или реплика тьютору."""
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    # Локальный импорт: turn и verify ссылаются друг на друга, а на уровне
    # модуля это цикл — verify не найдёт TurnReply в ещё не дочитанном turn.
    from llm_tutor.core import verify

    # Короткая реплика-команда разбирается ДО развилки: иначе при висящем
    # задании «пропусти» ушло бы в ветку ответа и записалось как неверный ответ.
    intent = intents.detect(user_text) if allow_intents else None
    if intent == "skip":
        return TurnReply(text=_skip_turn(conn, session_id, state, user_text, now=stamp))
    if intent == "close_topic":
        # Закрытие — отдельный проход; он сам запишет ход и состояние. Слова
        # ученика отдаём как есть: иначе в журнале остался бы синтетический
        # повод, и модель на следующем ходу не увидела бы реплики ученика.
        return verify.start_verification(conn, now=stamp, settings=s, user_text=user_text)
    force_stuck = intent == "stuck"
    options: list[str] | None = None
    # Второе сообщение хода — задание. Появится, если задание реально выдано:
    # отказ «заданий нет» остаётся в тексте, отдельным сообщением ему не быть.
    tail: str | None = None
    button: tuple[str, str] | None = None
    answered_item_id = state.pending_item_id

    pending_item = (
        repos.get_item(conn, state.pending_item_id)
        if state.pending_item_id is not None
        else None
    )
    is_answer = state.pending_item_id is not None and (
        # Нажатие кнопки (намерения выключены) — всегда ответ; задание
        # пропало из банка — пусть ветка ответа скажет «неактуально».
        not allow_intents
        or pending_item is None
        or lesson.answer_kind(pending_item, user_text) == "answer"
    )
    if is_answer and not force_stuck:
        reply, events, mastery, new_state, passed = await _answer_branch(
            conn, client, model, graph, user_text, state, now=stamp, settings=s
        )
        # Узел пройден? Только на ВЕРНОМ ответе: «не совсем верно» и «узёл
        # закрыт» в одном сообщении противоречат друг другу. Владение считаем
        # с учётом только что отвеченного задания, а не по старым данным.
        closed_note: str | None = None
        overrides = {
            change.concept_id: beta.to_mastery(
                change.concept_id,
                change.alpha,
                change.beta,
                change.last_seen,
                change.next_review,
            )
            for change in mastery
        }
        if passed:
            new_state, closed_note = _close_node_if_ready(
                conn, graph, new_state, overrides=overrides, now=stamp, settings=s
            )
        elif (
            passed is False
            and state.mode != "verify"
            and pending_item is not None
            and pending_item.answer_type in diagnostic.AUTO_CHECKABLE
        ):
            # Урок за ручку (срез 28): ошибка — разбор от тьютора, затем второй
            # шанс другим вопросом; вторая ошибка — тема слабая, ведём дальше.
            correct = (
                pending_item.options[int(pending_item.answer or 0)]
                if pending_item.answer_type == "choice"
                else str(pending_item.answer)
            )
            explanation, _, _, new_state, _ = await _tutor_branch(
                conn,
                client,
                model,
                session_id,
                lesson.MISTAKE_KICKOFF.format(given=user_text.strip(), correct=correct),
                new_state,
                graph,
                now=stamp,
                settings=s,
                allow_stuck=False,
            )
            if explanation != LLM_FAILURE_REPLY:
                reply = f"{reply}\n\n{explanation}"
            if state.check_misses >= 1:
                reply = f"{reply}\n\n{lesson.WEAK_NOTE}"
                new_state, closed_note = _close_node_if_ready(
                    conn, graph, new_state, overrides=overrides, now=stamp, settings=s,
                    force=True,
                )
            else:
                new_state = new_state.model_copy(update={"check_misses": 1})
        if closed_note:
            reply = f"{reply}\n\n{closed_note}"
            # Следующая тема — урок частями; заявленную (проход) не объясняем,
            # её вопрос выдаст общий `_issue_task` ниже.
            if new_state.current_node_id is not None and new_state.mode != "verify":
                explanation, new_state = await _start_lesson(
                    conn, client, model, session_id, new_state, graph, now=stamp, settings=s
                )
                reply = f"{reply}\n\n{explanation}"
                button = lesson.button(new_state)
        # Проход не подтвердился: обрываем его, тема уходит в обычный урок
        # частями. Ответ, который не удалось проверить (`None`), провалом не
        # считается — это сбой задания, а не пробел в знаниях ученика.
        if state.mode == "verify" and passed is False:
            new_state = guide.on_verification_failed(new_state)
            explanation, new_state = await _start_lesson(
                conn, client, model, session_id, new_state, graph, now=stamp, settings=s
            )
            reply = f"{reply}\n\n{VERIFY_FAILED_NOTE}\n\n{explanation}"
            button = lesson.button(new_state)
        # Задание: второй шанс или вопрос заявленной темы. После урока (кнопка)
        # и когда следующей темы нет (курс пройден, анкета модуля) — не выдаём.
        if button is None and not (closed_note and new_state.current_node_id is None):
            new_state, task_text, options = _issue_task(
                conn,
                graph,
                new_state,
                exclude_item_ids=(
                    frozenset({answered_item_id})
                    if answered_item_id is not None
                    else frozenset()
                ),
                now=stamp,
                settings=s,
            )
            # Задание уходит отдельным сообщением: признак выданного —
            # заполненный ``pending_item_id`` (у short вариантов нет).
            tail = task_text if new_state.pending_item_id is not None else None
            if tail is None and task_text:
                reply = f"{reply}\n\n{task_text}"
    else:
        # Вход в узел (§5.1): занятия ещё нет. Код берёт первый шаг маршрута,
        # тьютор объясняет именно его, и в том же ходу выдаётся первый тест —
        # ученику не нужно ничего писать, чтобы получить задание.
        entering = state.current_node_id is None and state.pending_item_id is None
        if entering:
            route = _fresh_route(conn, graph, state, now=stamp, settings=s)
            node_id = route_mod.next_node_id(conn, graph, route, now=stamp, settings=s)
            if node_id is None:
                entering = False  # маршрут исчерпан — входить некуда
            elif _is_claimed(route, node_id):
                # Заявленное в анкете не объясняем — подтверждаем проходом.
                return _enter_claimed(
                    conn, session_id, graph, state, route, node_id,
                    user_text=user_text, now=stamp, settings=s,
                )
            else:
                state = state.model_copy(
                    update={"current_node_id": node_id, "route": route}
                )

        if entering:
            # Урок за ручку (срез 28): тема объясняется частями, задание — только
            # после «Проверим». Ученик этого узла ещё не видел, поэтому ни «не
            # понял», ни «закрой тему» к нему не относятся.
            reply, new_state = await _start_lesson(
                conn, client, model, session_id, state, graph, now=stamp, settings=s
            )
            button = lesson.button(new_state)
            return _finish_turn(
                conn, session_id, graph, user_text, reply, new_state, button=button,
                now=stamp, settings=s,
            )
        reply, events, mastery, new_state, wants_close = await _tutor_branch(
            conn,
            client,
            model,
            session_id,
            user_text,
            state,
            graph,
            now=stamp,
            settings=s,
            force_stuck=force_stuck,
        )
        if wants_close:
            # Модель заметила просьбу закрыть тему: реплика тьютора и слова
            # ученика уходят в проход, он же пишет ход — и в ответ, и в журнал.
            #
            # Состояние, посчитанное `_tutor_branch` (hint_level, phase,
            # task_hinted), СОЗНАТЕЛЬНО не переносится: проход начинается с
            # чистого листа — закрытие должно опираться на свидетельства
            # прохода, а не на прежнюю лестницу подсказок и серию. Потерять
            # при этом нечего: события и владение `_tutor_branch` всегда
            # возвращает пустыми, а всё остальное проход задаёт сам и пишет
            # своим `post_turn`.
            return verify.start_verification(
                conn, now=stamp, settings=s, user_text=user_text, tutor_reply=reply
            )
        if state.pending_item_id is not None and not force_stuck:
            # Вопрос при висящем задании: ответили тьютором, задание не тронули.
            # При «не понял» напоминание не нужно — его заменяет STUCK_NOTE.
            reply = f"{reply}\n\n{PENDING_ITEM_NOTE}"
        else:
            # Реплика посреди урока задание НЕ выдаёт (срез 28): ученик спросил —
            # тьютор ответил, урок продолжается кнопкой с того же места.
            button = lesson.button(new_state)

    # Маршрут пересчитывается на каждом ходу, но ученику сообщается только
    # о значимом изменении (§6.4 — «малые различия» не тревожат план).
    fresh_route, route_note = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    new_state = new_state.model_copy(update={"route": fresh_route})
    if route_note:
        reply = f"{reply}\n\n{route_note}"

    post_turn(
        conn,
        session_id,
        user_text=user_text,
        # В журнал ход ложится целиком: `core/context` собирает хвост диалога
        # из журнала, и задание модель должна видеть — иначе на следующем ходу
        # она не поймёт, на что отвечает ученик.
        assistant_text=f"{reply}\n\n{tail}" if tail else reply,
        state=new_state,
        events=events,
        mastery=mastery,
        now=stamp,
    )
    return TurnReply(text=reply, options=options, tail=tail, button=button)


async def _start_lesson(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    session_id: int,
    state: SessionState,
    graph: CourseGraph,
    *,
    now: float,
    settings: Settings,
) -> tuple[str, SessionState]:
    """Вход в узел ``state.current_node_id``: урок частями, первая часть — сразу.

    Один вызов тьютора на тему; части лежат в состоянии, «Дальше» показывает
    их без модели. Сбой модели урок не блокирует: первой частью идёт описание
    темы из графа, а дальше — проверка.
    """
    node_id = state.current_node_id
    assert node_id is not None
    concept = graph.concept(node_id)
    fresh = state.model_copy(
        update={"lesson_parts": [], "lesson_part": 0, "check_misses": 0, "phase": "explain"}
    )
    reply, _, _, new_state, _ = await _tutor_branch(
        conn,
        client,
        model,
        session_id,
        lesson.LESSON_KICKOFF.format(name=concept.name),
        fresh,
        graph,
        now=now,
        settings=settings,
        allow_stuck=False,
    )
    if reply == LLM_FAILURE_REPLY:
        logger.warning("Урок «%s» без модели: показываю описание темы", node_id)
        parts = [f"📘 {concept.name}\n\n{concept.description or concept.name}"]
    else:
        parts = lesson.split_parts(reply)
    new_state = new_state.model_copy(update={"lesson_parts": parts, "lesson_part": 1})
    return parts[0], new_state


def _finish_turn(
    conn: sqlite3.Connection,
    session_id: int,
    graph: CourseGraph,
    user_text: str,
    reply: str,
    state: SessionState,
    *,
    button: tuple[str, str] | None,
    now: float,
    settings: Settings,
) -> TurnReply:
    """Пересчёт маршрута, запись хода и ответ без задания (ход урока)."""
    fresh_route, route_note = route_mod.refresh(conn, state, graph, now=now, settings=settings)
    if route_note:
        reply = f"{reply}\n\n{route_note}"
    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=reply,
        state=state.model_copy(update={"route": fresh_route}),
        now=now,
    )
    return TurnReply(text=reply, button=button)


def lesson_next_reply(
    conn: sqlite3.Connection,
    node_id: str,
    part: int,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Кнопка урока: следующая часть темы или, после последней, вопрос-проверка.

    Без модели. Колбэк несёт тему и число показанных частей — старая или
    повторная кнопка не совпадёт с состоянием и ничего не сдвинет.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    if (
        state.current_node_id != node_id
        or state.lesson_part != part
        or state.pending_item_id is not None
    ):
        return TurnReply(text=lesson.STALE_PART_REPLY)
    if part < len(state.lesson_parts):
        new_state = state.model_copy(update={"lesson_part": part + 1, "last_activity": stamp})
        text = state.lesson_parts[part]
        post_turn(
            conn,
            session_id,
            user_text=lesson.NEXT_KICKOFF_TEXT,
            assistant_text=text,
            state=new_state,
            now=stamp,
        )
        return TurnReply(text=text, button=lesson.button(new_state))
    graph = CourseGraph.load(conn)
    new_state, task_text, options = _issue_task(conn, graph, state, now=stamp, settings=s)
    post_turn(
        conn,
        session_id,
        user_text=lesson.NEXT_KICKOFF_TEXT,
        assistant_text=task_text,
        state=new_state,
        now=stamp,
    )
    return TurnReply(text=task_text, options=options)


def _close_node_if_ready(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    state: SessionState,
    *,
    overrides: Mapping[str, beta.Mastery] | None = None,
    now: float,
    settings: Settings,
    force: bool = False,
) -> tuple[SessionState, str | None]:
    """Закрывает пройденный узел и объявляет следующий.

    ``force`` — закрыть без критерия: вторая ошибка на проверке темы в уроке
    (срез 28) — ученика ведём дальше, тема остаётся слабым местом.

    Критерий считает код (``student/guide.py``), не модель: «понятно?» ответом
    доказательством не считается. ``overrides`` — владение С УЧЁТОМ только что
    отвеченного задания: без них и закрытие, и выбор следующего узла шли бы по
    старым данным (узел закрыт, а следующий по нему ещё «не готов»).
    """
    node_id = state.current_node_id
    if node_id is None:
        return state, None
    if not graph.has_node(node_id):
        # Узел убрали из графа (правка seed или БД руками) — иначе занятие
        # запиралось бы KeyError на каждом ходу, не записывая ответы.
        logger.warning("Узел %s больше не в графе, снимаю", node_id)
        return (
            state.model_copy(update={"current_node_id": None, "node_streak": 0}),
            None,
        )

    current = (overrides or {}).get(node_id) or beta.estimate(
        conn, node_id, now=now, settings=settings
    )
    if not force and not guide.is_node_closed(state, current, settings=settings):
        return state, None

    closed_name = graph.concept(node_id).name
    # Снимок достраивается до всего курса: у снимка до среза 25 в нём только
    # темы topic01, и закрытие последней объявило бы «курс пройден».
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        current_node_id=node_id,
        previous=state.route,
        now=now,
        settings=settings,
    )
    topic = route.topic_id if route.topic_id is not None else route_mod.choose_topic(graph, route)
    # Тема была закрыта и до этого хода (повтор из меню) — значит, это не
    # «последний шаг курса», даже если открытых тем нет (аудит среза 25, 25-L2).
    reclosed = any(
        step.concept_id == node_id and step.status == "closed" for step in route.steps
    )
    route = route.model_copy(
        update={
            "steps": [
                step.model_copy(update={"status": "closed", "closed_at": now})
                if step.concept_id == node_id
                else step
                for step in route.steps
            ]
        }
    )
    notes = [f"✅ Тема «{closed_name}» закрыта."]
    # «🎉» — один раз на модуль: возврат к пройденному его не повторяет.
    if (
        topic is not None
        and route_mod.topic_finished(graph, route, topic)
        and topic not in route.completed_topics
    ):
        route = route.model_copy(update={"completed_topics": [*route.completed_topics, topic]})
        notes.append(TOPIC_DONE_NOTE.format(number=topic, title=_topic_title(conn, topic)))
    next_topic = route_mod.choose_topic(graph, route, previous_topic=topic)
    route = route.model_copy(update={"topic_id": next_topic})
    if next_topic is not None and next_topic != topic:
        config = survey.config_for(conn, next_topic)
        if config is not None and not survey.is_completed(conn, config):
            # Вход в модуль — через его анкету: тему выберет урок после неё.
            notes.append(
                NEXT_TOPIC_SURVEY_NOTE.format(
                    number=next_topic, title=_topic_title(conn, next_topic)
                )
            )
            return (
                state.model_copy(
                    update={
                        "current_node_id": None,
                        "node_streak": 0,
                        "phase": "explain",
                        "mode": None,
                        "verify_item_ids": [],
                        "lesson_item_ids": [],
                        "hint_level": 0,
                        "check_misses": 0,
                        "lesson_parts": [],
                        "lesson_part": 0,
                        "route": route,
                    }
                ),
                "\n\n".join(notes),
            )
    next_node_id = route_mod.next_node_id(
        conn, graph, route, now=now, settings=settings, mastery_overrides=overrides
    )
    new_state = state.model_copy(
        update={
            "current_node_id": next_node_id,
            "node_streak": 0,
            "phase": "explain",
            "mode": None,
            # Закрытый узел не тащит за собой проверочный проход.
            "verify_item_ids": [],
            # Список выданного — про узел: новый узел начинает его заново.
            "lesson_item_ids": [],
            "hint_level": 0,
            "check_misses": 0,
            "lesson_parts": [],
            "lesson_part": 0,
            "route": route,
        }
    )
    if next_node_id is None:
        notes.append(COURSE_STILL_DONE_NOTE if reclosed else COURSE_DONE_NOTE)
        return new_state, "\n\n".join(notes)
    next_name = graph.concept(next_node_id).name
    if next_topic != topic:
        notes.append(
            NEXT_TOPIC_NOTE.format(
                number=next_topic, title=_topic_title(conn, next_topic), name=next_name
            )
        )
    if _is_claimed(route, next_node_id):
        notes.append(CLAIMED_CHECK_NOTE.format(name=next_name))
        return _checking(new_state, next_node_id), "\n\n".join(notes)
    if len(notes) == 1:
        return new_state, f"✅ Тема «{closed_name}» закрыта — идём дальше: {next_name}."
    return new_state, "\n\n".join(notes)


def _issue_task(
    conn: sqlite3.Connection,
    graph: CourseGraph,
    state: SessionState,
    *,
    exclude_item_ids: frozenset[int] = frozenset(),
    now: float,
    settings: Settings,
) -> tuple[SessionState, str, list[str] | None]:
    """Выдаёт задание по текущему узлу и переводит занятие в фазу практики.

    ``exclude_item_ids`` — задания, только что отвеченные в этом же ходу: их
    события ещё не записаны (запись идёт одним коммитом после), поэтому защита
    от повтора должна учитывать их явно.
    """
    route = _fresh_route(conn, graph, state, now=now, settings=settings)
    # Узел задан маршрутом: если его нет — берём следующий по приоритету.
    node_id = state.current_node_id or route_mod.next_node_id(
        conn, graph, route, now=now, settings=settings
    )
    if node_id is None:
        # Маршрут исчерпан — задания выдавать не по чему, и уходить в общий
        # подбор нельзя: он вернул бы узлы вне маршрута и сломал завершение.
        return (
            state.model_copy(update={"phase": "explain", "route": route, "last_activity": now}),
            ROUTE_DONE_REPLY,
            None,
        )

    # Задания, уже выданные в этом заходе по узлу. Сменили узел — список
    # начинается заново: он про текущую тему, а не про ученика вообще.
    lesson_used = (
        frozenset(state.lesson_item_ids) if state.current_node_id == node_id else frozenset()
    )

    if state.mode == "verify":
        # Проход: задание берём без паузы повтора, но не повторяем внутри прохода.
        question = diagnostic.verification_item(
            conn,
            node_id,
            used_item_ids=frozenset(state.verify_item_ids),
            now=now,
            settings=settings,
        )
    else:
        # Урок: пауза повтора не применяется, рубричные задания остаются проходу
        # (§5.3), а одно и то же задание не выдаётся дважды подряд.
        question = diagnostic.verification_item(
            conn,
            node_id,
            used_item_ids=lesson_used | exclude_item_ids,
            include_rubric=False,
            now=now,
            settings=settings,
        )
        if question is None and lesson_used:
            # Задания узла кончились, а серия ещё не набрана (например, оба
            # отвечены неверно) — идём по второму кругу. Без него узел с двумя
            # заданиями становился бы незакрываемым: оба выданы, серия нулевая.
            lesson_used = frozenset()
            question = diagnostic.verification_item(
                conn,
                node_id,
                used_item_ids=exclude_item_ids,
                include_rubric=False,
                now=now,
                settings=settings,
            )
    if question is None:
        if state.mode == "verify":
            # Проход не может продолжаться: снимаем режим, иначе ученик залип
            # в «проверяю» на каждом следующем задании.
            text = VERIFY_EXHAUSTED_REPLY if state.verify_item_ids else VERIFY_NO_ITEMS_REPLY
            return (
                state.model_copy(
                    update={
                        "mode": None,
                        "verify_item_ids": [],
                        "phase": "explain",
                        "route": route,
                        "last_activity": now,
                    }
                ),
                text,
                None,
            )
        return (
            state.model_copy(update={"phase": "explain", "route": route, "last_activity": now}),
            NO_TASK_FOR_NODE_REPLY,
            None,
        )

    new_state = state.model_copy(
        update={
            "pending_item_id": question.item.id,
            "current_node_id": question.concept_id,
            "hint_level": 0,
            # Новое задание — помощь по нему ещё не оказана.
            "task_hinted": False,
            "phase": "practice",
            "route": route,
            "last_activity": now,
            "verify_item_ids": (
                [*state.verify_item_ids, question.item.id]
                if state.mode == "verify"
                else state.verify_item_ids
            ),
            "lesson_item_ids": (
                [*lesson_used, question.item.id]
                if state.mode != "verify"
                else state.lesson_item_ids
            ),
        }
    )
    text = _render_item(question.item)
    if state.mode == "verify":
        header = VERIFY_STEP_TEMPLATE.format(
            name=graph.concept(node_id).name, step=len(state.verify_item_ids) + 1
        )
        if question.item.answer_type in diagnostic.RUBRIC_CHECKABLE:
            text = f"{header}\n\n{VERIFY_EXPLAIN_PREFIX}\n{text}"
        else:
            text = f"{header}\n\n{text}"
    return new_state, text, question.item.options or None


def start_practice_reply(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> TurnReply:
    """Выдаёт задание по текущему маршруту и ждёт ответа.

    Практика берёт и задания с рубрикой: открытые ответы проверяет грейдер.
    Если по текущему узлу заданий нет — говорим об этом честно, узел не рвём.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return TurnReply(text=EMPTY_GRAPH_REPLY)

    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    new_state, text, options = _issue_task(conn, graph, state, now=stamp, settings=s)

    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    post_turn(
        conn,
        session_id,
        user_text=PRACTICE_KICKOFF_TEXT,
        assistant_text=text,
        state=new_state.model_copy(update={"route": fresh_route}),
        now=stamp,
    )
    return TurnReply(text=text, options=options)


async def resume_reply(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
    start_node_id: str | None = None,
) -> TurnReply:
    """«Продолжить обучение»: занятие идёт с того места, где ученик остановился.

    Висящее задание не затирается — в этом отличие от `/task`, который молча
    выдаёт новое. ``start_node_id`` — с какого узла начать, если текущего ещё
    нет (первый урок после анкеты: тот, что объявлен в списке шагов).
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return TurnReply(text=EMPTY_GRAPH_REPLY)

    pending = state.pending_item_id
    if pending is not None and repos.get_item(conn, pending) is not None:
        post_turn(
            conn,
            session_id,
            user_text=RESUME_KICKOFF_TEXT,
            assistant_text=PENDING_ITEM_NOTE,
            state=state.model_copy(update={"last_activity": stamp}),
            now=stamp,
        )
        return TurnReply(text=PENDING_ITEM_NOTE)

    route = _fresh_route(conn, graph, state, now=stamp, settings=s)
    node_id = (
        state.current_node_id
        or start_node_id
        or route_mod.next_node_id(conn, graph, route, now=stamp, settings=s)
    )
    if node_id is None:
        post_turn(
            conn,
            session_id,
            user_text=RESUME_KICKOFF_TEXT,
            assistant_text=ROUTE_DONE_REPLY,
            state=state.model_copy(update={"route": route, "last_activity": stamp}),
            now=stamp,
        )
        return TurnReply(text=ROUTE_DONE_REPLY)

    if state.mode != "verify" and _is_claimed(route, node_id):
        return _enter_claimed(
            conn, session_id, graph, state, route, node_id,
            user_text=RESUME_KICKOFF_TEXT, now=stamp, settings=s,
        )

    if state.phase == "explain":
        # Урок уже идёт — повторяем последнюю показанную часть, модель не зовём.
        if state.current_node_id == node_id and 0 < state.lesson_part <= len(state.lesson_parts):
            text = state.lesson_parts[state.lesson_part - 1]
            return _finish_turn(
                conn, session_id, graph, RESUME_KICKOFF_TEXT, text, state,
                button=lesson.button(state), now=stamp, settings=s,
            )
        # Вход в узел: урок частями (срез 28), задание — после «Проверим».
        reply, new_state = await _start_lesson(
            conn, client, model, session_id,
            state.model_copy(update={"current_node_id": node_id}), graph,
            now=stamp, settings=s,
        )
        return _finish_turn(
            conn, session_id, graph, RESUME_KICKOFF_TEXT, reply, new_state,
            button=lesson.button(new_state), now=stamp, settings=s,
        )

    return start_practice_reply(conn, now=stamp, settings=s)


async def stuck_reply(
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    *,
    now: float | None = None,
    settings: Settings | None = None,
) -> TurnReply:
    """Просьба о глубине: углубляем текущий узел.

    Вход в усиленный проход на уровне ядра: кнопки «Не понимаю» больше нет,
    текстом её ловит слой намерения (``core.intents``) и приходит сюда же
    через ``_tutor_branch(force_stuck=True)``.

    Идёт тьюторским путём независимо от ``pending_item_id`` — иначе висящее
    задание приняло бы реплику за ответ.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    graph = CourseGraph.load(conn)

    reply, events, mastery, new_state, _ = await _tutor_branch(
        conn,
        client,
        model,
        session_id,
        STUCK_KICKOFF_TEXT,
        state,
        graph,
        now=stamp,
        settings=s,
        force_stuck=True,
    )
    fresh_route, _ = route_mod.refresh(conn, new_state, graph, now=stamp, settings=s)
    post_turn(
        conn,
        session_id,
        user_text=STUCK_KICKOFF_TEXT,
        assistant_text=reply,
        state=new_state.model_copy(update={"route": fresh_route}),
        events=events,
        mastery=mastery,
        now=stamp,
    )
    return TurnReply(text=reply)


def _skip_turn(
    conn: sqlite3.Connection,
    session_id: int,
    state: SessionState,
    user_text: str,
    *,
    now: float,
) -> str:
    """Снимает ожидание ответа, НЕ записывая свидетельство."""
    if state.pending_item_id is None:
        return NOTHING_TO_SKIP_REPLY
    post_turn(
        conn,
        session_id,
        user_text=user_text,
        assistant_text=SKIP_REPLY,
        state=state.model_copy(
            update={"pending_item_id": None, "phase": "explain", "last_activity": now}
        ),
        now=now,
    )
    return SKIP_REPLY


def skip_pending(
    conn: sqlite3.Connection, *, now: float | None = None, settings: Settings | None = None
) -> str:
    """Явный выход из задания командой `/skip`.

    Снимает ожидание ответа, НЕ записывая свидетельство: без него единственным
    способом выйти было ответить (и получить неверный ответ в журнал).
    """
    stamp = time.time() if now is None else now
    session_id = repos.ensure_open_session(conn, stamp)
    state = repos.get_session_state(conn, session_id)
    return _skip_turn(conn, session_id, state, SKIP_KICKOFF_TEXT, now=stamp)
