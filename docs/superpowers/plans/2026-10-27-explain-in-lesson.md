# «Объясни новичку» внутри занятия — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** «Объясни новичку» становится обязательным заданием после закрытия важного
занятия: вкладка «Объясни» в занятии, слева новичок, справа наставник в чате
тьютора, курс стоит, пока задание не выполнено.

**Architecture:** эталон переезжает в узел-занятие (`concepts.explain`). Висящее
задание **вычисляется** (`student/explain.pending_lesson`) по снимку маршрута,
эталонам, факту `explain_since` и завершённым обязательным сессиям — так оно
срабатывает при любом пути закрытия. Ядро (`core/explain`) работает по занятию:
обязательная сессия заводится сама, ход адресован новичку или наставнику,
сессия завершается сама по покрытию или лимиту, «Продолжить» начинает разминку
или следующее занятие через `core/turn`/`core/review`. API — в `web/api_lesson`
(развилка как у серии повторения), старые страницы и `api_explain` уходят.

**Tech Stack:** Python 3.12, FastAPI + Jinja2, pydantic 2, SQLite, JS без сборки,
pytest (`asyncio_mode=auto`), Playwright.

**Spec:** `docs/superpowers/specs/2026-10-27-explain-in-lesson-design.md` (база —
`docs/superpowers/specs/2026-10-24-explain-novice-design.md`).

## Global Constraints

- Комментарии, докстринги, коммиты — по-русски; коммиты `Срез N: …`, фиксы ревью — `Аудит среза N: …`. Срезы 92–96, ветка `claude/stoic-clarke-os47j2`.
- Слои: `web → core/student/db`. `core/explain` → `core/turn`, `core/review` (новая зависимость); `core/turn` → только `student/explain`, не `core/explain`.
- Сырой Markdown из `core`; в веб — `web.markdown.render` или `textContent`. CSP без inline; ширины — CSSOM.
- Схема — только миграцией `migrations/009_explain_lesson.sql`, `SCHEMA_VERSION = 9`.
- Эталон занятия: идей **2–4**, обязательных **≥ 1**, загвоздок **1–2**, `belief` кончается `?`, id уникальны в модуле.
- Важные занятия (ровно эти 14): `selection`, `grouping`, `eda_case`, `distributions`, `numeric_relations`, `tree_criteria`, `knn`, `validation`, `linreg`, `logreg`, `nonlinear`, `bagging_basics`, `forest`, `feature_importance`.
- Пороги TOML: `max_student_turns = 10`, `wrap_up_share = 1.0`, `pitfall_max = 1`, `pitfall_every = 2`; остальные прежние.
- Новичку эталон (`text`, `expect`, `fix`) и ответы наставника не передаются никогда; прежние тесты этого (включая враждебного оценщика) сохраняются, переписанные на занятие.
- Пока висит задание, ходы курса — 409 без записей: `lesson/next`, `task`, `answer`, `code-result`, `skip`, `stuck`, `resume`, `verify`, `switch`, `review/start`.
- Весь набор `uv run pytest -q` зелёный после каждой задачи.

## Review Focus

- **Последнее важное занятие модуля:** дальше анкета следующего модуля — задание обязано идти **раньше** анкеты (`lesson_state` проверяет задание до анкеты). Тест — в задаче 5.
- **F5 посреди задания:** состояние показывает ту же сессию, реплики новичка слева и ветку наставника справа без потерь. Тест — в задаче 5.
- **Закрыто сразу два важных занятия** (проход знакомого): задания идут по очереди, «Продолжить» после первого ведёт ко второму, а не к уроку. Тест — в задаче 4.
- **Добровольная сессия открыта, а важное занятие закрылось:** обязательная вытесняет добровольную (та завершается с отчётом), блокировка — по обязательной. Тест — в задаче 4.
- **Чат без задания:** `lesson/chat` идёт обычному тьютору урока, как раньше (регрессия развилки). Тест — в задаче 5.

---

### Task 1: Эталон на занятие — формат, проверка, миграция (срез 92)

**Files:**
- Create: `migrations/009_explain_lesson.sql`
- Modify: `src/llm_tutor/db/connection.py` (`SCHEMA_VERSION = 9`), `src/llm_tutor/schemas.py`, `src/llm_tutor/course/seed.py`, `src/llm_tutor/db/repos.py`
- Modify: `tests/fixtures/lessons/seed_topic01.json`, `seed_topic02.json` — `explain` у `l1_select`, `l1_group`, `l2_box`, `l2_corr`
- Test: `tests/test_explain_lesson_seed.py`

**Interfaces:**
- Produces (`schemas`): `Concept.explain: ExplainConfig | None = None`; у `ExplainIdea.lesson`/`ExplainPitfall.lesson` дефолт `""` (проставляет загрузчик); `ExplainSession.concept_id: str | None = None`, `ExplainSession.kind: Literal["required", "voluntary"] = "voluntary"`. `Topic.explain` пока остаётся (уходит в задаче 5).
- Produces (`course.seed`): `LESSON_MIN_IDEAS = 2`, `LESSON_MAX_IDEAS = 4`, `LESSON_MIN_REQUIRED = 1`, `LESSON_MIN_PITFALLS = 1`, `LESSON_MAX_PITFALLS = 2`; `_check_lesson_explain(seeds) -> list[str]`; загрузчик ставит `lesson = node.id` идеям и загвоздкам узла; указанный `lesson`, не равный узлу, — `SeedError`; предупреждение `f"В модуле {n} нет важных занятий («Объясни новичку»)"` для опубликованного модуля без эталонов занятий **и** без `topic.explain`.
- Produces (`db.repos`): `concepts.explain` пишется в `_write_concept` и читается в `get_concepts`; `get_explain_configs(conn) -> dict[str, ExplainConfig]` (активные узлы с эталоном); `create_explain_session(conn, topic_id, *, now, concept_id: str | None = None, kind: str = "voluntary", commit=True) -> int`; `finished_required(conn) -> set[str]` (`concept_id` завершённых `required`).

- [ ] **Step 1: Падающие тесты** — `tests/test_explain_lesson_seed.py` (копии фикстуры в `tmp_path`, как `test_explain_seed._course_with`):
  - `test_fixture_lessons_have_explain` — у `l1_select`, `l1_group`, `l2_box`, `l2_corr` `explain` есть, у остальных `None`; у каждой идеи `lesson == node.id`.
  - `test_lesson_ideas_bounds` — параметризованно: 1 идея и 5 идей → `SeedError` с `"идей"`.
  - `test_lesson_needs_required` — 0 обязательных → `SeedError` с `"обязательн"`.
  - `test_lesson_pitfalls_bounds` — 0 и 3 загвоздки → `SeedError` с `"загвоздок"`.
  - `test_belief_must_be_question` — `belief` без `?` → `SeedError` с id загвоздки.
  - `test_duplicate_ids_in_module` — один id в двух занятиях модуля → `SeedError`.
  - `test_foreign_lesson_field` — `"lesson": "l1_frames"` у идеи узла `l1_group` → `SeedError`.
  - `test_explain_reaches_db` — `load_lessons(conn)`; `repos.get_explain_configs(conn)` — ровно 4 ключа, равны данным курса.
  - `test_session_kind_and_lesson` — `create_explain_session(conn, 1, now=1.0, concept_id="l1_group", kind="required")` → `get_explain_session` отдаёт эти поля; `finished_required` до завершения — `set()`, после `update_explain_session(status="finished")` — `{"l1_group"}`; добровольная завершённая туда не попадает.

  Run: `uv run pytest -q tests/test_explain_lesson_seed.py` — FAIL (нет полей и функций).

- [ ] **Step 2: Реализовать.** Миграция — дословно из спеки («Схема»). Эталоны фикстур: по 2–3 идеи (≥ 1 обязательная) и 1–2 загвоздки на занятие; загвоздки — те, что сейчас в модульном `explain` фикстуры для этих занятий.

  Run: `uv run pytest -q tests/test_explain_lesson_seed.py && uv run pytest -q` — PASS.

- [ ] **Step 3: Коммит** — `Срез 92: эталон «Объясни новичку» на занятие, миграция 009`.

---

### Task 2: Эталон 14 важных занятий (срез 92)

**Files:**
- Modify: `data/seed_topic01.json` … `seed_topic05.json` — `explain` у 14 узлов, `topic.explain` удалить
- Modify: `scripts/check_explain_claims.py` (только если меняются id/формулировки), `tests/test_explain_content.py` (переписать)

**Interfaces:** Consumes Task 1 (формат и проверки).

- [ ] **Step 1: Падающие тесты** — `tests/test_explain_content.py` заменить:
  - `test_exactly_important_lessons_have_explain` — множество узлов курса с `explain` равно 14 id из Global Constraints.
  - `test_no_module_level_explain` — у всех `topic.explain is None`.
  - `test_no_explain_warnings` — в `load_course_data().warnings` нет «важных занятий».
  - `test_claims_script_ids_exist` — id из `@check("…")` в `scripts/check_explain_claims.py` (регэкспом по тексту файла) ⊆ id загвоздок курса.
  - `test_beliefs_are_questions` — прежний.

  Run: `uv run pytest -q tests/test_explain_content.py` — FAIL.

- [ ] **Step 2: Написать эталон.** Идеи и загвоздки модульного `explain` переносятся в свои занятия (по полю `lesson`), недостающее дописывается по конспекту `data/theory/topicNN/<занятие>.md`. Формат JSON — `indent=2`, как у файлов `data/`.

- [ ] **Step 3: Проверить утверждения запуском.**
  Run: `uv run --with numpy,pandas,scikit-learn,matplotlib python scripts/check_explain_claims.py` — `OK: N утверждений`. Новые загвоздки о поведении библиотек — новой проверкой в скрипте.

- [ ] **Step 4:** Run: `uv run pytest -q` — PASS. **Коммит** — `Срез 92: эталон 14 важных занятий`.

---

### Task 3: Висящее задание (срез 93)

**Files:**
- Modify: `src/llm_tutor/student/explain.py`, `src/llm_tutor/web/userdb.py`
- Test: `tests/test_explain_pending.py`

**Interfaces:**
- Consumes: Task 1 (`get_explain_configs`, `finished_required`).
- Produces (`student.explain`):
  - `SINCE_FACT = "explain_since"`
  - `mark_since(conn, now: float) -> None` — пишет факт, только если его нет (коммит)
  - `pending_lesson(conn, route: Route | None, order: Sequence[str]) -> str | None` — первое по `order` занятие: `closed` в `route`, есть в `get_explain_configs`, `closed_at is not None and closed_at >= since`, нет в `finished_required`; факта нет — `None`
- Produces (`userdb.UserDBPool.open`): вызывает `explain.mark_since(conn, time.time())` после `review.backfill`.

- [ ] **Step 1: Падающие тесты** — `tests/test_explain_pending.py` (`load_lessons`, маршрут из `RouteStep` вручную, `order` = `CourseGraph.load(conn).topo_order()`):
  - `test_pending_after_close` — `since=10`, `l1_group` закрыт с `closed_at=20` → `"l1_group"`.
  - `test_closed_before_since_ignored` — `closed_at=5` → `None`.
  - `test_not_important_ignored` — закрыт только `l1_frames` → `None`.
  - `test_finished_required_clears` — завершённая `required` по `l1_group` → `None`; завершённая `voluntary` — не снимает.
  - `test_earliest_by_order` — закрыты `l1_group` и `l1_select` (оба после `since`) → `"l1_select"`.
  - `test_no_fact_no_pending` — факта нет → `None`.
  - `test_reopened_lesson_not_pending` — статус `current`/`ahead` → `None`.
  - `test_mark_since_idempotent` — второй `mark_since` значение не меняет.
  - `test_userdb_open_marks_since` — `UserDBPool.open` на новой БД ставит факт.

  Run: `uv run pytest -q tests/test_explain_pending.py` — FAIL.

- [ ] **Step 2: Реализовать**, Run тот же файл и `uv run pytest -q` — PASS. **Коммит** — `Срез 93: висящее задание «Объясни новичку» вычисляется по закрытым важным занятиям`.

---

### Task 4: Ядро задания по занятию и граница занятий (срез 93)

**Files:**
- Modify: `src/llm_tutor/core/explain.py` (переписать модульный поток на занятие), `src/llm_tutor/core/turn.py`, `src/llm_tutor/schemas.py` (`ExplainState.mode` убрать, `ExplainState.proceeded: bool = False`), `src/llm_tutor/llm/prompt_config.py` (`ALLOWED_FIELDS["novice.system"] = {"title", "known"}`), `data/prompts/explain.toml` (пороги из Global Constraints; промпт новичка — про одно занятие; `{lessons}` убрать), `src/llm_tutor/web/views.py` (`explain_session_view` без `mode`; `explain_modules`/флаг `explain` у модулей убрать)
- Delete: `src/llm_tutor/web/api_explain.py`, `src/llm_tutor/web/templates/explain.html`, маршруты `/lesson/{id}/explain*` в `pages.py` (вместо них — 303 на `/lesson/{id}`), `tests/test_web_explain.py`, `tests/test_web_explain_pages.py`; `app.py` — без `api_explain`
- Test: переписать `tests/test_explain_turn.py`, `tests/test_explain_session.py` на занятие; `tests/test_explain_boundary.py`

**Interfaces:**
- Consumes: Task 3 (`pending_lesson`), `turn._warmup`, `review._begin_lesson`, `turn.post_turn`.
- Produces (`core.explain`):
  - `ExplainError(code)`, коды: `not_available`, `other_active`, `not_active`, `not_found`, `llm`, `required` (обязательную нельзя закончить), `not_done` («Продолжить» до выполнения)
  - `LessonContext(node_id, topic_id, title, config, known: list[str], theory: list[str])` — `known`: названия закрытых занятий раньше этого в порядке курса
  - `blocking(conn) -> tuple[str, str] | None` — `(node_id, название)`, если висит задание или есть завершённая обязательная с `proceeded = False`
  - `ensure_required(conn, prompts, *, now, settings) -> ExplainSession | None` — по `pending_lesson`; активная добровольная завершается (`button`); первая реплика — `opening` с названием занятия; повторный вызов — та же сессия; активная обязательная по занятию без эталона — закрыть `stale`
  - `start_voluntary(conn, prompts, *, node_id, now, settings) -> ExplainSession` — `not_available` (не закрыто/не важное), `other_active` (есть активная или висит обязательная)
  - `turn(conn, client, prompts, *, session_id, text, to: Literal["novice", "mentor"], now, settings) -> ExplainSession` — после хода новичку: все обязательные `correct` → `finish(end_reason="coverage")`, 10-я реплика → `"limit"`, тем же коммитом
  - `stop(conn, *, session_id, now, settings) -> ExplainReport | None` — только добровольная, иначе `required`
  - `proceed(conn, client, prompts, *, now, settings) -> TurnReply` — нет завершённой обязательной без `proceeded` → `not_done`; ставит `proceeded = True`; если `pending_lesson` снова есть — `ensure_required` и `EXPLAIN_NOTE`; иначе `turn._warmup` (с `previous = state.route`) или `review._begin_lesson`; запись — `turn.post_turn` с `user_text = PROCEED_TEXT = "Продолжить."`
  - `close_stale` — только добровольные (24 ч); `finish`, `share_enabled`, `set_share` — прежние
  - Уходят: `ModuleContext`, `availability`, `module_reason`, `passed_modules`, `start`, `set_mentor`
- Produces (`core.turn`): `EXPLAIN_NOTE = "Перед тем как идти дальше — объясни новичку «{name}» своими словами: вкладка «Объясни»."`; в блоке `closed_note` `handle_turn` — если `explain_logic.pending_lesson(...)` не `None`, разминка и урок следующего занятия **не** начинаются, задание урока не выдаётся, к ответу — `EXPLAIN_NOTE`; то же примечание — в ветке автозакрытия `_issue_task`.

- [ ] **Step 1: Падающие тесты** — `tests/test_explain_boundary.py` (`load_lessons`, `mark_since(conn, 0.0)`, `finish_all_but(conn, "l1_group", topic_id=1)`, `GradingTutor`/`ScriptedTutor` из `web_fakes`):
  - `test_closing_important_lesson_holds_next` — верный ответ закрывает `l1_group`: в ответе `EXPLAIN_NOTE`, урок `l2_*` не начат (`lesson_parts == []`), `pending_item_id is None`, `blocking(conn)[0] == "l1_group"`.
  - `test_verify_pass_also_triggers` — проход знакомого закрывает важное занятие → `pending_lesson` не `None`.
  - `test_autoclose_without_items_triggers` — занятие без заданий закрывается в `_issue_task` → задание есть, в тексте `EXPLAIN_NOTE`.
  - `test_unimportant_lesson_no_task` — закрытие `l1_frames` → урок следующего занятия начинается, как раньше.
  - `test_ensure_required_idempotent_and_opening` — два вызова → одна сессия `kind="required"`, `concept_id="l1_group"`, первая реплика — `opening` с названием занятия.
  - `test_auto_finish_on_coverage` — наблюдение с верными цитатами по всем обязательным → `status="finished"`, `end_reason="coverage"`, реплика новичка записана.
  - `test_auto_finish_on_limit` — 10 ходов → `end_reason="limit"`.
  - `test_mentor_turn_keeps_coverage` — `to="mentor"`: реплика наставника, `student_turns` не растёт, `mentor_ideas` пополнены.
  - `test_stop_required_refused` — `stop` по обязательной → `ExplainError("required")`; по добровольной — отчёт.
  - `test_proceed_before_done_refused` — `ExplainError("not_done")`.
  - `test_proceed_starts_lesson` — после завершения `proceed` → урок следующего занятия начат (`lesson_parts` не пусты), `blocking(conn) is None`.
  - `test_proceed_starts_warmup_first` — с просроченной строкой расписания → `state.review.kind == "warmup"`.
  - `test_two_tasks_in_a_row` — закрыты `l1_select` и `l1_group`: после первого `proceed` — новая обязательная по `l1_group`, урок не начат (Review Focus).
  - `test_required_displaces_voluntary` — открыта добровольная по `l1_select`, закрылось `l1_group` → `ensure_required` завершает добровольную (`button`, отчёт есть) и заводит обязательную (Review Focus).
  - `test_voluntary_rules` — по незакрытому или неважному → `not_available`; при висящей обязательной → `other_active`.
  - `test_novice_sees_lesson_only` — в сообщениях новичка есть название занятия и `known`, нет оглавления модуля и строк эталона.

  Переписать `test_explain_turn.py`/`test_explain_session.py`: сессия по `l1_group` через `ensure_required`; `turn(..., to=...)` вместо `set_mentor`; тесты модульного доступа (`availability`, `test_module_reopened_mid_session`, `test_start_*`) удалить, остальные (ограждение, враждебный оценщик, неозвученные установки, откат, вставка, лимит хвоста) сохранить.

  Run: `uv run pytest -q tests/test_explain_boundary.py tests/test_explain_turn.py tests/test_explain_session.py` — FAIL.

- [ ] **Step 2: Реализовать.** Удалить старые веб-файлы и тесты по списку «Delete», страницы `/lesson/{id}/explain` и `/lesson/{id}/explain/{sid}` — `RedirectResponse(f"/lesson/{id}", 303)`.

  Run: те же файлы, затем `uv run pytest -q` — PASS.

- [ ] **Step 3: Коммит** — `Срез 93: задание «Объясни новичку» по занятию — граница занятий, автозавершение, «Продолжить»`.

---

### Task 5: API занятия и блок состояния (срез 94)

**Files:**
- Modify: `src/llm_tutor/web/api_lesson.py`, `src/llm_tutor/web/views.py`, `src/llm_tutor/schemas.py` (`Topic.explain` убрать), `src/llm_tutor/course/seed.py` (модульные правила `_check_explain` убрать), `src/llm_tutor/db/repos.py` (`topics.explain` больше не пишется и не читается), фикстуры `tests/fixtures/lessons/*.json` (`topic.explain` убрать), `tests/test_explain_seed.py` (удалить — заменён задачей 1)
- Test: `tests/test_web_explain_lesson.py`

**Interfaces:**
- Consumes: Task 4 (`blocking`, `ensure_required`, `start_voluntary`, `turn`, `stop`, `proceed`, `share_enabled`, `set_share`).
- Produces (API, всё под `ctx.lock`): `POST lesson/explain/turn {text}` (`text` 1–8000, не пробелы), `POST lesson/explain/start {node_id}` (400 — `not_available`, 409 — `other_active`), `POST lesson/explain/stop {}` (409 — `required`), `POST lesson/explain/continue {}` (409 — `not_done`), `GET lesson/explain/sessions/{id}`, `POST lesson/explain/settings {share}`. `lesson/chat` при активной сессии → `turn(..., to="mentor")`. Ходы из Global Constraints при `blocking(conn)` → 409 с `f"Сначала объясни новичку «{name}»."`. Ответы ходов — `turn_response` (реплики + свежее состояние).
- Produces (`views.lesson_state`): `ensure_required` перед сборкой состояния (под замком — `GET lesson/state` берёт замок); при `blocking` — проверка **до** анкеты, `node = None`, `item = None`, `messages` — ветка наставника (`mentor`/`student`-к-наставнику как `assistant`/`user`); блок `explain` по спеке (`active`, `pending`, `ready`, `lessons`, `history`, `share`).

- [ ] **Step 1: Падающие тесты** — `tests/test_web_explain_lesson.py` (`use_lessons_course`, `ExplainLLM`, анкета через API, `finish_all_but` + верный ответ закрывает `l1_group`):
  - `test_state_shows_required_task` — после закрытия `state.explain.active.node_id == "l1_group"`, `kind == "required"`, `state.node is None`, `state.item is None`.
  - `test_blocked_moves_409` — параметризованно по 10 ходам из Global Constraints: 409, число событий не изменилось.
  - `test_novice_turn` — `explain/turn` → реплики ученика и новичка, `progress`.
  - `test_chat_goes_to_mentor` — `lesson/chat` при задании → ответ наставника (вызов `chat` у `ExplainLLM`), в `messages` урока (`repos.get_messages`) реплики не добавились.
  - `test_chat_without_task_is_tutor` — без задания `lesson/chat` идёт тьютору (`ScriptedTutor`), как раньше (Review Focus).
  - `test_reload_keeps_threads` — два `GET lesson/state` подряд после ходов новичку и наставнику дают одинаковые `explain.active.messages` и `messages` (Review Focus).
  - `test_task_before_next_module_survey` — закрыто последнее важное занятие модуля 1 при непройденной анкете модуля 2 → `state.survey is False`, `state.explain.active` есть (Review Focus).
  - `test_continue_flow` — `continue` до выполнения → 409; после автозавершения → 200, `state.explain.active is None`, `state.node` — следующее занятие.
  - `test_voluntary_start_stop` — без задания `start {node_id: "l1_group"}` (закрыт) → активная `voluntary`, ходы курса **не** блокируются; `stop` → отчёт; `start` по незакрытому → 400.
  - `test_whitespace_422`, `test_llm_failure_502` (реплика не записана), `test_settings_share`.
  - `test_old_explain_pages_redirect` — `GET /lesson/mlcourse/explain` → 303 на `/lesson/mlcourse`.

  Run: `uv run pytest -q tests/test_web_explain_lesson.py` — FAIL.

- [ ] **Step 2: Реализовать**; убрать `Topic.explain` и модульные проверки (фикстуры — без `topic.explain`). Run тот же файл и `uv run pytest -q` — PASS.

- [ ] **Step 3: Коммит** — `Срез 94: «Объясни новичку» в API занятия — блокировка, наставник в чате, «Продолжить»`.

---

### Task 6: Интерфейс — вкладка «Объясни» и наставник справа (срез 95)

**Files:**
- Modify: `src/llm_tutor/web/templates/lesson.html` (пятая вкладка `tab-btn-explain` + панель `tab-explain`; убрать ссылку «Объясни новичку» из шапки), `src/llm_tutor/web/static/js/lesson.js` (закрытые вкладки при `state.explain.active?.kind === "required"` или `state.explain.ready`: `aria-disabled`, подпись «откроется после объяснения», выбор «Объясни»), `src/llm_tutor/web/static/js/chat.js` (при активной сессии — заголовок «Наставник», плейсхолдер «Спроси наставника…»; кнопка переключателя вида `view-chat` на телефоне — «Наставник», без задания — «Чат»), `src/llm_tutor/web/static/js/explain.js` (переписать модулем вкладки по образцу `review.js`: `Lesson.onState`, ходы через `Lesson.act("explain/turn", …)`), `src/llm_tutor/web/static/js/route.js` (убрать ссылку у модуля), `src/llm_tutor/web/static/css/explain.css` (подключить в `lesson.html`)
- Test: `tests/test_web_lesson_explain_tab.py`

**Interfaces:** Consumes Task 5 (блок `state.explain`, ходы). Вкладка рисует: при активной — шапку («Объясни новичку: «…». Конспект откроется после задания» — только для `required`), полоску (`progress`, CSSOM), чат новичка (`html` с сервера, своё — `textContent`), поле ввода; добровольная — кнопку «Закончить»; при `ready` — отчёт и «Продолжить»; иначе — историю, «Объяснить ещё раз» по `lessons`, «Что сохраняется» и переключатель согласия.

- [ ] **Step 1: Падающие тесты** — `tests/test_web_lesson_explain_tab.py`: в HTML страницы занятия есть `tab-btn-explain` и `tab-explain`, подключён `js/explain.js` и `css/explain.css`; нет ссылки `/explain` в шапке; нет inline `<script>` и `style="`. Run — FAIL.
- [ ] **Step 2: Реализовать.** Проверка синтаксиса: `node -e "for (const f of ['explain','chat','lesson','route']) new Function(require('fs').readFileSync('src/llm_tutor/web/static/js/'+f+'.js','utf8'))"` — без ошибок. Run тесты и `uv run pytest -q` — PASS.
- [ ] **Step 3: Коммит** — `Срез 95: вкладка «Объясни» — новичок слева, наставник в чате справа`.

---

### Task 7: Админка, документы, E2E (срез 96)

**Files:**
- Modify: `src/llm_tutor/web/admin.py`, `templates/admin_student.html`, `templates/admin_explain.html` (занятие и тип вместо модуля; старые модульные — «Модуль N»), `README.md`, `llm-tutor-architecture.md` (§7.1), `CLAUDE.md`, `tests/test_web_admin_explain.py` (сессии по занятию), `tests/e2e/test_explain.py` (переписать), `tests/e2e/helpers.py`, `tests/e2e/conftest.py` (`_serve` принимает `prompts_dir`; фикстура сервера для E2E задания кладёт копию `data/prompts` с `max_student_turns = 2`)
- Test: `tests/test_web_admin_explain.py`, `tests/e2e/test_explain.py`

- [ ] **Step 1: Падающие тесты:**
  - `test_web_admin_explain.py`: `test_card_lists_lesson_sessions` — в карточке название занятия и «обязательное»/«добровольное»; `test_legacy_module_session_listed` — сессия без `concept_id` — «Модуль 1». Остальные тесты согласия — прежние.
  - `tests/e2e/test_explain.py::test_required_task_flow` — анкета, `finish_all_but`-аналог через прямую запись БД (закрыть всё до `l1_group`, висит задание по `l1_group`) → верный ответ закрывает занятие → вкладка «Объясни» выбрана, остальные `aria-disabled` → две реплики новичку → вопрос в правый чат (подпись «🧑‍🏫 Наставник») → завершение по лимиту (лимит через `PROMPTS_DIR` тестового сервера = 2) → отчёт → «Продолжить» → вкладки открыты, в шапке следующее занятие.
  Run — FAIL.
- [ ] **Step 2: Реализовать**; документы: README (раздел и API под занятием), архитектура §7.1 (задание после важных занятий, блокировка, вычисляемое задание), CLAUDE.md (таблица «Где что», подводный камень: висящее задание вычисляется; `core/explain → core/turn`; конспект скрыт сервером; «Продолжить»).
  Run: `uv run pytest -q && uv run pytest -q -m e2e tests/e2e/test_explain.py` — PASS.
- [ ] **Step 3: Коммит** — `Срез 96: админка, документы и E2E задания «Объясни новичку»`.
