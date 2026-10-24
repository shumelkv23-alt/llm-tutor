# Повторение по расписанию — план реализации (срезы 79–83)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Лестница повторений по занятиям, разминка из 1–2 вопросов на границе занятий, вкладка «Повторение» и число «к повторению» на главной.

**Architecture:** Расписание — таблица `review_schedule` и чистые функции `student/review.py` (изменения считаются, пишет ход одним коммитом). Серия — поле `SessionState.review` в сессии урока; ходы серии — `core/review.py`, который импортирует `core/turn` (обратного импорта нет). Веб разводит ходы в `web/api_lesson.py`: пока висит задание повторения, ответ, код, чат, пропуск и «Застрял» уходят в `core/review`.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pydantic 2, pytest (`asyncio_mode=auto`), JS без сборки, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-22-spaced-review-design.md`

## Уточнения к спеке (решены при чтении кода)

1. **Строка расписания создаётся в `core.turn.post_turn`, а не в `_close_node_if_ready`.** Занятие закрывается на четырёх путях (ответ, ответ раздела, итоговое, «заданий нет» в `_issue_task`), и все пишут ход через `post_turn`. Там прошлое состояние читается из БД до записи, разница маршрутов «стало закрыто» даёт новые строки — тем же коммитом.
2. **Разминка ставится в `handle_turn`, в блоке `if closed_note:`** — там, где сейчас начинается следующее занятие (`_start_lesson` или задание прохода). Закрытие «заданий нет» в `_issue_task` разминку не ставит (редкий путь недописанного урока).
3. **Ошибка в повторении называет раздел текстом** («Освежить: раздел „…“ занятия „…“»), без ссылки: на странице занятия виден конспект только текущего занятия.
4. Литерал `"review"` в `NodeMode` остаётся: он лежит в сохранённых снимках маршрутов.

## Global Constraints

- Комментарии, докстринги, коммиты — по-русски; коммит `Срез N: …`, фиксы ревью `Аудит среза N: …`; в конце коммита — строки атрибуции сессии.
- Слои: `web → core/student/db`; `core/*` и `student/*` не импортируют `web/*`; `core/turn` не импортирует `core/review`.
- Схема БД — только миграцией `migrations/007_review.sql`; новые поля состояния — в `SessionState` с дефолтами.
- Лестница **1, 3, 7, 14, 30, 60 дней**; лимит сроков **10 на календарный день UTC**; после проверочного прохода — **ступень 2**; ошибка — **ступень −2, не ниже 0, срок +1 день**; верно на ступени 60 дней — строка удаляется.
- Разминка — **до 2** занятий; серия «Срочное» — **до 5** занятий; по одному заданию на занятие; итоговое (`final`) не берётся; отвеченное за **24 часа** не берётся.
- Свидетельство — как в уроке: новое задание — 1.0, уже отвеченное — `repeat_evidence_weight`, рубрика — `rubric_evidence_weight`; подсказка влияет только на лестницу.
- Факты: `review_enabled` (`"0"`/`"1"`, нет факта — включено), `review_backfill = "1"`.
- CSP без `unsafe-inline`; `core.*` отдаёт сырой Markdown; новый текст в JS — через `textContent`.
- Тесты не зависят от `.env` (фикстура `settings`); веб — фикстуры `web`/`student`, курс — `use_lessons_course`.
- `uv run pytest -q` зелёный после каждой задачи.

## Review Focus

1. **Старый ученик без снимка маршрута** (`state.route is None` или снимок до среза 25) — `backfill` не падает, строк не создаёт, факт ставит; тест в задаче 3.
2. **Строка есть, а занятия в графе нет / модуль снят** — не попадает ни в серию, ни в число на главной; тест в задаче 2.
3. **Две вкладки**: в одной идёт серия, из другой приходит ответ на задание урока — 409 «неактуально», ни события, ни зачёта уроку; тест в задаче 8.
4. **Результат кода после «Закончить»** — 409, событий нет; тест в задаче 8.
5. **Повторение выключено, курс пройден за день** — строки всё равно создаются, включение показывает просроченное; тест в задаче 3.

---

## Срез 79 — расписание

### Task 1: Таблица и арифметика лестницы

**Files:**
- Create: `migrations/007_review.sql`, `src/llm_tutor/student/review.py`
- Modify: `src/llm_tutor/db/repos.py` (функции расписания), `src/llm_tutor/schemas.py` (`ReviewRow`)
- Test: `tests/test_review_schedule.py`

**Interfaces:**
- Produces:
  - `schemas.ReviewRow(BaseModel)`: `concept_id: str`, `step: int`, `due_at: float`.
  - `repos.get_review_rows(conn) -> dict[str, ReviewRow]`, `repos.upsert_review(conn, concept_id, step, due_at, *, commit=True)`, `repos.delete_review(conn, concept_id, *, commit=True)`.
  - `student/review.py`: `LADDER_DAYS = (1.0, 3.0, 7.0, 14.0, 30.0, 60.0)`, `DAILY_LIMIT = 10`, `VERIFY_START_STEP = 2`, `RECENT_ITEM_SECONDS = 86_400.0`, `WARMUP_SIZE = 2`, `SERIES_SIZE = 5`.
  - `@dataclass(frozen=True) ScheduleChange`: `concept_id: str`, `step: int | None`, `due_at: float | None` (`step is None` — удалить строку).
  - `utc_day(ts: float) -> int`.
  - `first_due(rows: Mapping[str, ReviewRow], *, step: int, now: float, pending: Sequence[ScheduleChange] = ()) -> float` — первый день от `utc_day(now) + LADDER_DAYS[step]`, где сроков (строки + `pending`) меньше `DAILY_LIMIT`; время суток — как у `now`.
  - `plan_answer(row: ReviewRow | None, concept_id: str, *, passed: bool, hinted: bool, due: bool, now: float) -> ScheduleChange | None`.
  - `plan_postpone(rows: Mapping[str, ReviewRow], concept_ids: Iterable[str], *, now: float) -> list[ScheduleChange]` — ступень та же, срок `now + DAY`; занятия без строки пропускаются.
  - `write_changes(conn, changes: Iterable[ScheduleChange], *, commit: bool = True) -> None`.

- [ ] **Step 1: Тесты арифметики**

```python
DAY = 86_400.0
NOW = 100 * DAY + 3_600.0  # 01:00 UTC сотого дня

def test_first_due_tomorrow_when_free():
    assert review.first_due({}, step=0, now=NOW) == NOW + DAY

def test_first_due_skips_full_day():
    rows = {f"n{i}": ReviewRow(concept_id=f"n{i}", step=0, due_at=NOW + DAY) for i in range(10)}
    assert review.first_due(rows, step=0, now=NOW) == NOW + 2 * DAY

def test_first_due_counts_pending():
    pending = [review.ScheduleChange(f"n{i}", 0, NOW + DAY) for i in range(10)]
    assert review.first_due({}, step=0, now=NOW, pending=pending) == NOW + 2 * DAY

def test_first_due_verify_step_starts_week_later():
    assert review.first_due({}, step=2, now=NOW) == NOW + 7 * DAY

def test_due_correct_moves_up():
    row = ReviewRow(concept_id="a", step=1, due_at=NOW - 1)
    assert review.plan_answer(row, "a", passed=True, hinted=False, due=True, now=NOW) == review.ScheduleChange("a", 2, NOW + 7 * DAY)

def test_correct_on_last_step_retires():
    row = ReviewRow(concept_id="a", step=5, due_at=NOW - 1)
    assert review.plan_answer(row, "a", passed=True, hinted=False, due=True, now=NOW) == review.ScheduleChange("a", None, None)

def test_mistake_drops_two_steps_floor_zero():
    for step, expected in ((4, 2), (1, 0), (0, 0)):
        row = ReviewRow(concept_id="a", step=step, due_at=NOW - 1)
        assert review.plan_answer(row, "a", passed=False, hinted=False, due=True, now=NOW) == review.ScheduleChange("a", expected, NOW + DAY)

def test_hinted_correct_keeps_step_tomorrow():
    row = ReviewRow(concept_id="a", step=3, due_at=NOW - 1)
    assert review.plan_answer(row, "a", passed=True, hinted=True, due=True, now=NOW) == review.ScheduleChange("a", 3, NOW + DAY)

def test_early_correct_changes_nothing():
    row = ReviewRow(concept_id="a", step=3, due_at=NOW + 5 * DAY)
    assert review.plan_answer(row, "a", passed=True, hinted=False, due=False, now=NOW) is None

def test_mistake_without_row_creates_step_zero():
    assert review.plan_answer(None, "a", passed=False, hinted=False, due=False, now=NOW) == review.ScheduleChange("a", 0, NOW + DAY)

def test_correct_without_row_changes_nothing():
    assert review.plan_answer(None, "a", passed=True, hinted=False, due=False, now=NOW) is None

def test_postpone_keeps_step_skips_missing():
    rows = {"a": ReviewRow(concept_id="a", step=3, due_at=NOW - 1)}
    assert review.plan_postpone(rows, ["a", "b"], now=NOW) == [review.ScheduleChange("a", 3, NOW + DAY)]

def test_write_changes_upsert_and_delete(conn):
    # conn — фикстура БД с мигрированной схемой и курсом (load_lessons)
    review.write_changes(conn, [review.ScheduleChange("pandas_structures", 0, NOW)])
    assert repos.get_review_rows(conn)["pandas_structures"].step == 0
    review.write_changes(conn, [review.ScheduleChange("pandas_structures", None, None)])
    assert repos.get_review_rows(conn) == {}
```

(id занятия — из `tests/fixtures/lessons/seed_topic01.json`; взять реальный.)

- [ ] **Step 2: Прогнать — падают** (`uv run pytest -q tests/test_review_schedule.py`, ожидается `ModuleNotFoundError`/`ImportError`).

- [ ] **Step 3: Миграция**

```sql
-- Миграция 007: повторение по расписанию (срез 79).
-- Строка — закрытое занятие на лестнице повторений (student/review.LADDER_DAYS).
-- mastery.next_review расписанием больше не служит.
CREATE TABLE IF NOT EXISTS review_schedule (
  concept_id TEXT PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,
  step       INTEGER NOT NULL DEFAULT 0,
  due_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS review_schedule_due ON review_schedule(due_at);
```

- [ ] **Step 4: Реализовать** `ReviewRow`, три функции `repos` (по образцу `upsert_mastery`, флаг `commit`) и функции `student/review.py` из Interfaces. В `plan_answer` порядок правил: нет строки → ошибка даёт ступень 0 через день, иначе `None`; ошибка → `max(0, step − 2)`, `now + DAY`; верно с подсказкой → ступень та же, `now + DAY`; верно и `due` → ступень +1 (после 5 — удалить), `now + LADDER_DAYS[step + 1] · DAY`; верно и не `due` → `None`.

- [ ] **Step 5: Прогнать** — `uv run pytest -q tests/test_review_schedule.py tests/test_db.py` зелёные.

- [ ] **Step 6: Коммит** `Срез 79: таблица review_schedule и лестница повторений`.

### Task 2: Что готово к повторению и какое задание дать

**Files:**
- Modify: `src/llm_tutor/student/review.py`, `src/llm_tutor/db/repos.py`
- Test: `tests/test_review_schedule.py`

**Interfaces:**
- Consumes: Task 1; `diagnostic._available_items(conn, include_rubric=True, include_runnable=True)`; `seed.main_concept(item)`; `beta.estimate`.
- Produces:
  - `repos.last_answer_ts(conn) -> dict[int, float]` — `MAX(ts)` событий по `item_id`.
  - `review.pick_item(conn, concept_id: str, *, now: float, exclude: Collection[int] = ()) -> Item | None` — задания с `main_concept == concept_id`, не `final`, не в `exclude`, не отвеченные за `RECENT_ITEM_SECONDS`; сначала никогда не отвеченные (по id), затем по давности ответа.
  - `review.due_lessons(conn, graph: CourseGraph, route: Route | None, *, now: float, settings: Settings) -> list[str]` — закрытые в `route`, есть в `graph`, `due_at <= now`, `pick_item` не `None`; порядок: просрочка по убыванию, затем `beta.estimate(...).mean` по возрастанию.
  - `review.closed_lessons(graph, route) -> list[str]` — закрытые занятия маршрута, которые есть в графе (для «Освежить»).

- [ ] **Step 1: Тесты** (`tests/test_review_schedule.py`, курс — `load_lessons(conn)`, маршрут собирается `Route(steps=[RouteStep(concept_id=…, mode="full", status="closed", closed_at=…)])`):
  - `test_pick_item_skips_final_and_recent` — у занятия ответили на задание A час назад: возвращается не A и не `final`.
  - `test_pick_item_prefers_never_answered_then_oldest` — все отвечены: возвращается отвеченное раньше всех.
  - `test_pick_item_none_when_only_final` — у занятия остались только `final` и свежие: `None`.
  - `test_due_lessons_order` — две строки просрочены на 3 и 1 день: порядок `[3 дня, 1 день]`; при равной просрочке первым идёт занятие с меньшим `mean`.
  - `test_due_lessons_skips_not_closed_missing_and_empty` — строка у незакрытого занятия, у занятия, которого нет в переданном графе (`CourseGraph(nodes без него, edges)` — так выглядит снятый с публикации модуль), и у занятия без подходящих заданий — все трое не в результате.

- [ ] **Step 2: Прогнать — падают.**

- [ ] **Step 3: Реализовать** функции из Interfaces.

- [ ] **Step 4: Прогнать — зелёные.**

- [ ] **Step 5: Коммит** `Срез 79: занятия к повторению и выбор задания`.

### Task 3: Строки при закрытии, перенос старых учеников, переключатель

**Files:**
- Modify: `src/llm_tutor/student/review.py`, `src/llm_tutor/core/turn.py` (`post_turn`), `src/llm_tutor/web/userdb.py` (`open`)
- Test: `tests/test_review_schedule.py`, `tests/test_turn.py` (или новый `tests/test_review_turn.py`)

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `review.plan_closures(rows: Mapping[str, ReviewRow], before: Route | None, after: Route | None, *, verify_node_id: str | None, now: float) -> list[ScheduleChange]` — занятия, закрытые в `after` и не закрытые в `before`, без строки; ступень `VERIFY_START_STEP`, если это `verify_node_id` или шаг маршрута в режиме `skip`, иначе 0; срок — `first_due` с учётом уже набранных изменений.
  - `review.backfill(conn, route: Route | None, *, now: float) -> None` — один раз (факт `review_backfill`): закрытые без строки; ступень по давности `closed_at` (< 3 дней — 0, < 7 — 1, < 14 — 2, иначе 3), срок — `max(closed_at + LADDER_DAYS[step]·DAY, now + DAY)` с лимитом дня; пишет и коммитит.
  - `review.enabled(conn) -> bool`, `review.set_enabled(conn, flag: bool, *, commit: bool = True) -> None`.
  - `post_turn(...)`: перед записью состояния читает `old = repos.get_session_state(conn, session_id)` и пишет `plan_closures(rows, old.route, state.route, verify_node_id=old.current_node_id if old.mode == "verify" else None, now=stamp)` с `commit=False`. Плюс новый аргумент `schedule: Sequence[ScheduleChange] = ()` — изменения от ходов повторения, пишутся тем же коммитом.

- [ ] **Step 1: Тесты**
  - `test_plan_closures_new_lesson_step_zero`, `test_plan_closures_verify_step_two`, `test_plan_closures_existing_row_untouched` (повторное закрытие), `test_plan_closures_eleven_in_a_day_spill_over` (11 закрытий за ход: 10 на завтра, 1 на послезавтра).
  - `test_closing_lesson_creates_row` — сквозной: на фикстуре занятий закрыть занятие ответами (как в `tests/test_lesson_sections.py`: `_enter`, `_check`, `_answer`) — у занятия строка со ступенью 0 и сроком `now + DAY`.
  - `test_closure_rolls_back_with_turn` — `post_turn` с падающей записью (monkeypatch `repos.update_session_state` бросает) — строки нет.
  - `test_backfill_steps_by_age_and_once` — маршрут с занятиями, закрытыми 1, 5, 10 и 40 дней назад: ступени 0, 1, 2, 3; второй вызов ничего не меняет; факт `review_backfill == "1"`.
  - `test_backfill_without_route` — `route=None`: строк нет, факт поставлен (Review Focus 1).
  - `test_rows_created_when_disabled` — `set_enabled(conn, False)`, закрыть занятие: строка есть; `set_enabled(conn, True)`: `due_lessons` в момент срока её видит (Review Focus 5).
  - `test_userdb_open_runs_backfill` — в `tests/test_userdb.py`: ученик со снимком маршрута с закрытым занятием, `pool.open` — строка появилась после `remap_progress`.

- [ ] **Step 2: Прогнать — падают.**

- [ ] **Step 3: Реализовать.** В `UserDBPool.open` после `remap_progress`: `review.backfill(conn, state.route, now=time.time())`, где `state` — состояние открытой сессии (`repos.get_open_session` → `get_session_state`; нет сессии — `None`).

- [ ] **Step 4: Прогнать** — `uv run pytest -q` целиком зелёный.

- [ ] **Step 5: Коммит** `Срез 79: строка расписания при закрытии занятия и перенос старых учеников`.

### Task 4: Просрочка больше не открывает занятие

**Files:**
- Modify: `src/llm_tutor/student/planner.py` (`_choose_mode`, `mode_for_node`, слагаемое `_urgency` в приоритете), `src/llm_tutor/student/route.py:255-259`, `src/llm_tutor/student/beta.py` (докстринг `_next_review`: пометка, что расписание — `review_schedule`)
- Test: `tests/test_planner.py`, `tests/test_route.py`; переписать тесты старого поведения в `tests/test_lesson.py`, `tests/test_lesson_gaps.py`, `tests/test_web_parity.py`, `tests/test_beta.py` (найти: `grep -rn "next_review\|\"review\"\|overdue" tests`)

**Interfaces:**
- Produces: `planner._choose_mode(mastery, *, weak_soft_prereq, stuck, settings)` — без `overdue`; режим `"review"` планировщик больше не выдаёт.

- [ ] **Step 1: Тесты**
  - `test_overdue_closed_lesson_stays_closed` (`test_route.py`) — закрытое занятие с `next_review` в прошлом: после `build_route` статус `closed`.
  - `test_overdue_does_not_raise_priority` (`test_planner.py`) — два готовых узла, у одного просрочен `next_review`: порядок `ready_nodes` тот же, что без просрочки.
  - `test_failures_still_reopen` — два провала после закрытия по-прежнему открывают занятие (`REOPEN_FAILURES`).
- [ ] **Step 2: Прогнать — новые падают.**
- [ ] **Step 3: Реализовать**; тесты старого поведения переписать на новое (не удалять проверки, а менять ожидание).
- [ ] **Step 4: Прогнать** `uv run pytest -q` — зелёный.
- [ ] **Step 5: Коммит** `Срез 79: просрочка повторения не переоткрывает занятие` — в теле перечислить переписанные тесты.

---

## Срез 80 — серия в ядре

### Task 5: Состояние серии, выдача и ответ

**Files:**
- Create: `src/llm_tutor/core/review.py`
- Modify: `src/llm_tutor/schemas.py` (`ReviewState`, поле `SessionState.review`), `src/llm_tutor/core/turn.py` (вынести проверку из `_answer_branch`; выдача задания повторения)
- Test: `tests/test_review_core.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `schemas.ReviewState` — поля как в спеке (`kind`, `queue`, `node_id`, `item_id`, `hinted`, `total`, `correct`, `answered`); `SessionState.review: ReviewState | None = None`.
  - `turn.grade_item(conn, client, model, item: Item, user_text: str, *, grade: GradeResult | None, settings: Settings) -> GradeResult | None` — тело проверки из `_answer_branch` (`None` — задание не проверяется); `_answer_branch` зовёт её, поведение урока не меняется.
  - `turn.issue_review_item(conn, state: SessionState, *, now: float, exclude: Collection[int] = ()) -> tuple[SessionState, str | None, list[str] | None]` — снимает занятия с головы `state.review.queue`, пока `review.pick_item` не даст задание; кладёт `node_id`/`item_id`, `hinted=False`; текст — `REVIEW_STEP_HEADER` + `_render_item`. Очередь пуста — `(state, None, None)`.
  - `turn.REVIEW_STEP_HEADER = "🔁 Повторение · «{name}» · {position} из {total}"`.
  - `core/review.py`:
    - `start(conn, *, kind: Literal["due", "refresh"], node_ids: Sequence[str] = (), now: float | None = None, settings: Settings | None = None) -> TurnReply` — `due`: первые `SERIES_SIZE` из `due_lessons`; `refresh`: `node_ids`, отфильтрованные по `closed_lessons` (чужие — `ValueError`). Нечего спросить — `NOTHING_TO_REVIEW_REPLY` и `review=None`.
    - `async answer(conn, client, model, text: str, *, grade: GradeResult | None = None, now=None, settings=None) -> TurnReply`.
    - `skip(conn, *, now=None, settings=None) -> TurnReply`.
    - `abort(conn, *, now=None) -> None` — снять серию без реплики (одним коммитом состояния).
    - тексты: `NOTHING_TO_REVIEW_REPLY = "Повторять пока нечего — пройденное ещё свежо."`, `REVIEW_DONE_TEMPLATE = "Повторение закончено: верно {correct} из {answered}."`, `REVIEW_MISTAKE_NOTE = "Освежить: раздел «{section}» занятия «{name}»."`.
  - `answer`: проверка `grade_item`; концепт — `main_concept(item)`; свидетельство — `diagnostic.plan_evidence` с весом как в `_answer_branch` (повтор/рубрика); расписание — `review.plan_answer(row, …, due=row is not None and row.due_at <= now)`; ошибка — верный ответ для `choice`/`short` и `REVIEW_MISTAKE_NOTE`; затем `issue_review_item` или итог `REVIEW_DONE_TEMPLATE` (`review=None`); всё — один `post_turn(..., schedule=…)`.

- [ ] **Step 1: Тесты** (`tests/test_review_core.py`, фикстура занятий, занятие X закрыто и просрочено, ученик стоит в занятии Y посреди раздела):
  - `test_start_due_issues_item_of_due_lesson` — `item_id` из занятия X, `state.review.kind == "due"`.
  - `test_answer_credits_item_lesson_not_current` — после верного ответа событие с `concept_id == X`, а у Y событий не прибавилось.
  - `test_lesson_fields_untouched` — `state.model_dump(exclude={"review", "last_activity", "route"})` до `start` и после итога серии равны.
  - `test_correct_due_answer_moves_step`, `test_mistake_drops_step_and_names_section` (в тексте есть название раздела задания).
  - `test_repeat_item_weighted_as_lesson` — уже отвеченное задание: вес события `== settings.repeat_evidence_weight`.
  - `test_stale_item_taken_next` — висящее задание погашено (`repos.replace_seed` без него / `UPDATE items SET active=0`): ответ даёт `STALE_ITEM_REPLY` и следующее задание очереди.
  - `test_skip_postpones_day` — `skip`: событий нет, `due_at == now + DAY`, следующее задание выдано.
  - `test_series_done_summary` — после последнего ответа текст `"верно 1 из 1"`, `state.review is None`.
  - `test_refresh_rejects_unclosed` — `start(kind="refresh", node_ids=[Y])` — `ValueError`.
  - `test_nothing_to_review` — просроченного нет: `NOTHING_TO_REVIEW_REPLY`, `review is None`.
- [ ] **Step 2: Прогнать — падают.**
- [ ] **Step 3: Реализовать**; сначала вынести `grade_item` и убедиться, что `tests/test_turn.py` зелёный без правок.
- [ ] **Step 4: Прогнать** `uv run pytest -q` — зелёный.
- [ ] **Step 5: Коммит** `Срез 80: серия повторения в ядре — выдача, ответ, пропуск`.

### Task 6: Чат и «Застрял» во время серии

**Files:**
- Modify: `src/llm_tutor/core/review.py`, `src/llm_tutor/core/turn.py` (`post_turn`: метка реплик), `src/llm_tutor/core/context.py` (метка в хвосте диалога)
- Test: `tests/test_review_core.py`, `tests/test_context.py`

**Interfaces:**
- Consumes: Task 5; `turn._tutor_branch`; `intents.detect`.
- Produces:
  - `async review.chat(conn, client, model, text: str, *, stuck: bool = False, focus: Focus | None = None, now=None, settings=None) -> TurnReply` — намерение `skip` → `skip`; `close_topic` → `abort` и `verify.start_verification`; иначе `_tutor_branch` на **виде состояния**: копия с `current_node_id = review.node_id`, `section_id = item.section`, `pending_item_id = review.item_id`, `force_stuck=stuck`; из результата берётся только факт помощи → `review.hinted = True`; поля урока — из исходного состояния. К ответу — `turn.PENDING_ITEM_NOTE`.
  - `post_turn(..., review_node_id: str | None = None)` — в `meta` обеих реплик ключ `"review": review_node_id` (вместе с `quote`/`task_chars`).
  - `context.REVIEW_TAIL_LABEL = "(повторение занятия «{name}») "` — префикс реплик с `meta.review` в хвосте диалога.
- [ ] **Step 1: Тесты**
  - `test_chat_context_uses_review_section` — подставной тьютор (`tests/web_fakes.py` или `_FakeTutor`) запоминает `messages`; в системном промпте текст раздела задания X (`seed.section_text`), а не Y.
  - `test_chat_marks_hinted_keeps_step` — вопрос, затем верный ответ: ступень не выросла, срок `now + DAY`.
  - `test_stuck_marks_hinted`.
  - `test_chat_skip_intent_skips_review_item`.
  - `test_review_messages_meta_and_tail_label` (`test_context.py`) — реплики серии несут `meta.review`, а в хвосте следующего хода урока стоит `REVIEW_TAIL_LABEL`.
- [ ] **Step 2–4:** прогнать (падают) → реализовать → `uv run pytest -q` зелёный.
- [ ] **Step 5: Коммит** `Срез 80: чат и «Застрял» во время повторения`.

---

## Срез 81 — разминка

### Task 7: Разминка на границе занятий

**Files:**
- Modify: `src/llm_tutor/core/turn.py` (`handle_turn`, блок `if closed_note:`), `src/llm_tutor/core/review.py`
- Test: `tests/test_review_warmup.py`

**Interfaces:**
- Consumes: Tasks 2, 5.
- Produces:
  - `turn.WARMUP_NOTE = "Перед «{next}» — разминка: вспомним пройденное."`
  - В `handle_turn`: если `new_state.current_node_id` не `None`, `review.enabled(conn)` и `review.due_lessons(...)` не пуст — `review = ReviewState(kind="warmup", queue=due[:WARMUP_SIZE], total=…)`, `issue_review_item`, задание — в `tail`; `_start_lesson` и задание прохода **не** зовутся, кнопки нет. Иначе — как сейчас.
  - `async review.stop(conn, client, model, *, now=None, settings=None) -> TurnReply` — снять серию; для `warmup` — `review.plan_postpone` оставшихся (включая висящее) и начать занятие: `turn._issue_task`, если `state.mode == "verify"`, иначе `turn._start_lesson` + `guided.button`. Конец разминки ответом в `answer` ведёт так же.
  - Отсрочка — `review.plan_postpone` из Task 1.
- [ ] **Step 1: Тесты** (`tests/test_review_warmup.py`):
  - `test_warmup_instead_of_lesson_start` — закрыть Y при просроченном X: в ответе `WARMUP_NOTE` и задание X, `lesson_parts == []`, `review.kind == "warmup"`.
  - `test_no_warmup_when_disabled`, `test_no_warmup_at_course_end` (закрыто последнее занятие), `test_no_warmup_before_module_survey`.
  - `test_warmup_at_most_two` — три просроченных: `total == 2`.
  - `test_lesson_starts_after_warmup` — ответ на последнее задание разминки: итог серии и первая часть нового занятия, `state.lesson_part == 1`, кнопка урока.
  - `test_verify_pass_after_warmup` — следующее занятие `claimed`: после разминки выдано задание прохода.
  - `test_stop_warmup_postpones` — `stop`: сроки оставшихся `now + DAY`, новое занятие началось; закрыть следующее в тот же день — разминки нет.
- [ ] **Step 2–4:** прогнать (падают) → реализовать → `uv run pytest -q` зелёный.
- [ ] **Step 5: Коммит** `Срез 81: разминка из повторений перед новым занятием`.

---

## Срез 82 — веб

### Task 8: Ходы API и состояние занятия

**Files:**
- Modify: `src/llm_tutor/web/api_lesson.py`, `src/llm_tutor/web/views.py` (`lesson_state`, `course_card`)
- Test: `tests/test_web_review.py`

**Interfaces:**
- Consumes: Tasks 5–7.
- Produces:
  - `POST /courses/{course_id}/lesson/review/start` — тело `ReviewStartBody(kind: Literal["due", "refresh"], node_ids: list[str] = [])`; 409 до анкеты (`_require_survey`) и при идущей серии; 400 на `ValueError` из `review.start`.
  - `POST /courses/{course_id}/lesson/review/stop` — без тела; нет серии — 409.
  - `POST /courses/{course_id}/lesson/review/settings` — `ReviewSettingsBody(enabled: bool)`.
  - Развилка при `state.review` с `item_id`: `answer`/`code-result` — `body.item_id != review.item_id` → `STALE_ITEM_STATUS`; иначе `review.answer` (для кода — с `grade`), `verdict` как сейчас; `chat` → `review.chat`; `skip` → `review.skip`; `stuck` → `review.chat(..., stuck=True)`; `next` при `warmup` → `review.stop`, иначе `review.abort` и обычный ход; `switch`/`task`/`verify` → `review.abort`, затем обычный ход; `resume` при серии — текущее задание повторения без изменений.
  - Задание урока при идущей серии (`body.item_id == state.pending_item_id`, но не `review.item_id`) — `STALE_ITEM_STATUS`.
  - `views.lesson_state(...)["review"] = {"enabled": bool, "due": int, "active": {"kind", "node_name", "position", "total"} | None, "lessons": [{"module", "id", "name", "due"}]}`; при идущей серии `"item"` — задание повторения.
  - `views.course_card(...)["review_due"]: int` — 0 у незаписанного, выключенного, ошибочного курса.
  - Шаги маршрута в `lesson_state` получают `"review_due": bool` (занятие из `due_lessons`) — для пометки «пора повторить».
- [ ] **Step 1: Тесты** (`tests/test_web_review.py`, по образцу `tests/test_web_fragment.py`; фикстуры `web`, `student`, `use_lessons_course`):
  - `test_start_due_and_answer`, `test_start_409_when_active`, `test_start_409_before_survey`, `test_refresh_400_on_unclosed`, `test_draft_course_404`, `test_review_post_requires_origin`.
  - `test_lesson_item_answer_stale_during_review` (Review Focus 3) — ответ с `item_id` урока: 409, событий не прибавилось.
  - `test_code_result_after_stop_is_stale` (Review Focus 4).
  - `test_switch_aborts_review`, `test_next_in_warmup_stops`, `test_state_has_review_block`, `test_settings_toggle`.
  - `test_card_review_due_counts_and_hides_disabled`, `test_card_skips_missing_lesson` (Review Focus 2).
- [ ] **Step 2–4:** прогнать (падают) → реализовать → `uv run pytest -q` зелёный.
- [ ] **Step 5: Коммит** `Срез 82: ходы повторения в API и блок review в состоянии занятия`.

### Task 9: Вкладка «Повторение», плашка, главная

**Files:**
- Create: `src/llm_tutor/web/static/js/review.js`
- Modify: `src/llm_tutor/web/templates/lesson.html` (вкладка `tab-btn-review` / панель `tab-review` после «Кода»), `src/llm_tutor/web/static/js/practice.js` (плашка над заданием и кнопка «Пропустить разминку»/«Закончить»), `src/llm_tutor/web/static/js/lesson.js` (передача `state.review` во вкладку), `src/llm_tutor/web/static/js/route.js` (пометка «пора повторить» у шага с `review_due`), `src/llm_tutor/web/templates/_course_card.html`, `src/llm_tutor/web/templates/home.html` (блок «Повторить сегодня»), `src/llm_tutor/web/static/css/lesson.css`
- Test: `tests/test_lesson_interface.py` (разметка вкладки), `tests/test_web_courses.py` (блок на главной)

**Interfaces:**
- Consumes: Task 8 (`state.review`, ходы `review/*`, `card.review_due`).
- Produces: `review.js` экспортирует `renderReview(panel, review, act)`; ходы — через существующий `act` из `lesson.js`. Ссылка блока на главной — `/lesson/{course_id}#review`; `lesson.js` по `#review` открывает вкладку.
- [ ] **Step 1: Тесты** — страница занятия содержит `id="tab-btn-review"` с `aria-controls="tab-review"` и подключает `js/review.js` через `static_url`; главная при `review_due > 0` показывает «Повторить сегодня» и ссылку `/lesson/<id>#review`, при 0 — блока нет.
- [ ] **Step 2–4:** прогнать (падают) → реализовать (тексты — `textContent`; `style=""` и встроенных `<script>` нет) → `uv run pytest -q` зелёный.
- [ ] **Step 5: Проверить в браузере** (навык `run`): вкладка, «Начать», ответ, «Закончить», главная; тёмная тема.
- [ ] **Step 6: Коммит** `Срез 82: вкладка «Повторение» и блок на главной`.

---

## Срез 83 — документы и E2E

### Task 10: E2E и документы

**Files:**
- Create: `tests/e2e/test_review.py`
- Modify: `tests/e2e/helpers.py` (шаги «закрыть занятие», «ответить в Практике»), `CLAUDE.md`, `llm-tutor-architecture.md` (§6.4)
- [ ] **Step 1: E2E** (маркер `e2e`): закрыть занятие при просроченном пройденном → в «Практике» плашка «Повторение» → ответ → начинается следующее занятие; вкладка «Повторение» → «Начать» → итог «верно N из M».
- [ ] **Step 2: Прогнать** `uv run pytest -q -m e2e tests/e2e/test_review.py` — зелёный.
- [ ] **Step 3: Документы.** CLAUDE.md, «Подводные камни»: повторение — `review_schedule`, а не `mastery.next_review`; ответ повторения засчитывается `main_concept` задания; развилка ходов в `api_lesson` при `state.review`; `core/turn` не импортирует `core/review`; строки создаёт `post_turn` по разнице маршрутов. Таблица «Где что»: `student/review`, `core/review`, `static/js/review.js`. Архитектура §6.4 — лестница вместо `next_review`.
- [ ] **Step 4: Прогнать** `uv run pytest -q` — зелёный.
- [ ] **Step 5: Коммит** `Срез 83: документы и E2E повторения`.

После каждого среза — адверсариальное ревью отдельным агентом и фиксы коммитом `Аудит среза N: …` с регрессионными тестами (процесс из CLAUDE.md).
