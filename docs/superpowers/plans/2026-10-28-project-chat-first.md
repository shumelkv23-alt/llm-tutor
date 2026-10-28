# Проект по курсу: разговор вместо форм — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** рабочая страница проекта становится разговором с наставником: карточку идеи и итог этапа наставник предлагает из переписки, ученик подтверждает одной кнопкой («Подтвердить и дальше») или правит в окне; слева — широкий чат, справа — сводка и полоса этапов.

**Architecture:** `ProjectMentorReply.proposal` в статусе `build` несёт заметки текущего этапа; их чистит `student/project.clean_proposal(..., stage, student_text)` со стражем чисел; новый ход `core/project.accept_stage` пишет заметки и переход этапа одним коммитом; API `POST …/project/accept`; вид проекта отдаёт предложение объектом с `kind`/`fields`/`actionable`; страница перестроена (чат слева, сводка справа, окна `<dialog>`). Миграций нет.

**Tech Stack:** Python 3.12, FastAPI + Jinja2, pydantic 2, SQLite, JS без сборки, pytest (`asyncio_mode=auto`), Playwright.

**Spec:** `docs/superpowers/specs/2026-10-28-project-chat-first-design.md` (основа — `2026-10-27-course-project-design.md`)

## Global Constraints

- Комментарии, докстринги, коммиты — по-русски; коммиты `Срез N: …` (срезы 102–105), фиксы ревью — `Аудит среза N: …`. Ветка `claude/great-pasteur-e8w5ce`.
- Слои: `web → core/student/db`; `core/project` не импортирует `core/turn`, `core/explain`, `web/*`; `student/project` не трогает БД и модель.
- Предложение итога этапа — поля текущего этапа шаблона, **кроме `code`** (`STAGE_PROPOSAL_EXCLUDED = frozenset({"code"})`).
- Страж чисел: число — `\d+(?:[.,]\d+)?`, сравнение после замены `,` на `.`; поле с числом, которого нет в тексте ученика, выбрасывается целиком; `student_text=None` — стража нет.
- Текст ученика для стража: реплики ученика этого проекта с `meta.stage` = текущему этапу плюс текущая реплика.
- `accept_stage`: значения — из сохранённой реплики; нехватка обязательных заметок → `ProjectError("missing", [...])`, ничего не пишется; иначе заметки и переход — один коммит.
- Правила длины и обязательности — прежние (`MIN_TEXT = 10`, `NOTE_MAX = 4000`, `CODE_MAX = 8000`, карточка — как была).
- Всё, что написал ученик, идёт модели в ограждении `core.context.fence`; на странице значения предложений — `textContent`.
- CSP без inline: никаких `<script>` в шаблоне и `style=""`; ширины — CSSOM (`split.js`).
- Быстрая кнопка: `idea` — «Предложи формулировки карточки», `build` — «Подвести итог этапа», `done` — нет (`quick_reply: null`).
- Ширины по умолчанию: чат ≈ 62 %, сводка ≈ 38 %; на телефоне по умолчанию открыт «Наставник».
- Весь набор `uv run pytest -q` зелёный после каждой задачи.

## Review Focus

- **Модель предлагает итог с цифрой, которой ученик не называл** («ROC AUC 0.91», а ученик писал 0.79) — поле выброшено, в `meta` его нет. Тест — задача 1.
- **Предложение итога с чужими ключами, `code` и HTML** — в `meta` только поля текущего этапа без `code`; на странице — как текст. Тесты — задачи 1 и 3.
- **«Подтвердить» по старому предложению** (этап уже сменился во второй вкладке) — 409, заметки и этап не меняются. Тест — задача 2.
- **Предложение без обязательного поля** — `accept` отвечает 422 со списком и ничего не пишет; страница открывает «Поправить» с подсветкой. Тесты — задачи 1 и 4.
- **Модель недоступна в `build`** — этап закрывается через «Записать итог самому» (`PUT notes` → `POST complete`), ученик не заперт. Тест — задача 4 (E2E).

---

### Task 1: Ядро — предложение итога, страж чисел, «Подтвердить и дальше» (срез 102)

**Files:**
- Modify: `src/llm_tutor/student/project.py`, `src/llm_tutor/core/project.py`, `data/prompts/project.toml`
- Test: `tests/test_project_logic.py`, `tests/test_project_core.py`

**Interfaces:**
- Consumes: `TEMPLATES`, `Stage`, `Field`, `notes_missing`, `next_stage`, `stage_of` (`student/project`); `ProjectError`, `_current`, `_merge`, `_write`, `_template` (`core/project`); `ProjectLLM` (`tests/web_fakes.py`).
- Produces (`student.project`): `STAGE_PROPOSAL_EXCLUDED: frozenset[str]`; `clean_proposal(template: ProjectTemplate, proposal: Mapping | None, *, stage: Stage | None = None, student_text: str | None = None) -> dict[str, str]`.
- Produces (`core.project`): `accept_stage(conn, *, turn_id: int, now: float) -> Project`; `_finish_stage(conn, project: Project, notes: dict[str, str], now: float) -> Project` (общая часть с `complete_stage`); `chat` в `build` пишет в `meta` наставника `{"stage": <id>, "proposal": {...}}`.
- Ошибки `accept_stage`: статус не `build` → `wrong_status`; реплика не наставника этого проекта или без `proposal` → `not_found`; `meta.stage` ≠ текущему → `wrong_stage`; нехватка → `missing`.

- [ ] **Step 1: Падающие тесты логики** — `tests/test_project_logic.py`:
  - `test_stage_proposal_fields` — `clean_proposal(ML, {"model": " Дерево ", "metric": "ROC AUC", "code": "x=1", "title": "T", "junk": "y"}, stage=stage_of(ML, "baseline"))` → `{"model": "Дерево", "metric": "ROC AUC"}`.
  - `test_number_guard` — `stage=baseline`, `proposal={"model": "Дерево глубины 5", "metric": "ROC AUC 0.91"}`, `student_text="дерево depth=5, auc 0,79"` → `{"model": "Дерево глубины 5"}` (5 есть, 0.91 нет; «0,79» = «0.79»).
  - `test_number_guard_off` — тот же вызов с `student_text=None` → оба поля.
  - `test_card_proposal_unchanged` — без `stage` поведение прежнее: только поля карточки.

- [ ] **Step 2:** `uv run pytest -q tests/test_project_logic.py` — FAIL (`stage` не принимается).

- [ ] **Step 3: Реализовать `clean_proposal`** в `student/project.py` по сигнатуре выше: `stage` задан → поля `stage.fields` без `STAGE_PROPOSAL_EXCLUDED`, иначе `template.card`; страж — после чистки, по `re.findall(r"\d+(?:[.,]\d+)?", …)` с заменой `,` → `.`.

- [ ] **Step 4: Падающие тесты ядра** — `tests/test_project_core.py` (фикстуры `prompts`, `course`, `brief`, хелперы `_pass_course`, `_start`, `_chat`, `_to_build`, `_turns` — уже есть):
  - `test_build_proposal_saved` — в `build` `ProjectLLM` отвечает `proposal={"done": "Загрузил CSV и посмотрел типы", "findings": "Ушло 26 процентов", "code": "df.head()"}`, ученик пишет «загрузил csv, ушло 26 процентов» → у реплики наставника `meta == {"stage": "data", "proposal": {"done": …, "findings": …}}` (без `code`).
  - `test_build_proposal_number_guard` — ученик писал «ушло примерно четверть», модель предлагает `findings: "Ушло 26 %"` → `findings` нет в `meta.proposal`.
  - `test_accept_stage_completes` — после предложения с обоими обязательными `accept_stage(conn, turn_id=<id реплики>, now=40.0)` → `state.notes["data"]` = значения из реплики, `stage == "baseline"`, `done_stages == ["data"]`.
  - `test_accept_stage_last_is_done` — на этапе `conclusions` → статус `done`, `done_at == now`.
  - `test_accept_stage_missing_writes_nothing` — предложение только с `done` → `ProjectError` с `code == "missing"`, `missing == ["findings"]`; `repos.current_project(conn).state` не изменился.
  - `test_accept_stage_errors` — чужой `turn_id` / реплика ученика / реплика без предложения → `not_found`; предложение этапа `data` после перехода на `baseline` → `wrong_stage`; проект в `idea` → `wrong_status`.
  - `test_mentor_sees_stage_fields` — в системном сообщении `mentor_messages` в `build` есть строка с `model` и подписью «Какая модель и почему», и нет `code` в перечне полей итога.
  - `test_idea_proposal_unchanged` — в `idea` `meta == {"proposal": {...}}` без `stage`, как раньше.

- [ ] **Step 5:** `uv run pytest -q tests/test_project_core.py` — FAIL.

- [ ] **Step 6: Реализовать** в `core/project.py`: `chat` — в `build` чистить `proposal` с `stage=` текущим и `student_text=` (реплики ученика с `meta.stage` = текущему + `text`); `_finish_stage` — вынести из `complete_stage` переход и запись; `accept_stage` по Interfaces; `_phase_block` в `build` — строка «Поля итога этапа (ключи для proposal): `key` — подпись (обязательное)» для полей этапа без `code`. В `project.toml` (`system`) заменить правило «НЕ пишешь за ученика текст выводов или заметок» и описание `proposal` по спеке («Наставник»): в `build` `proposal` — итог этапа пересказом слов ученика, поля без сказанного не заполнять; ключи — из блока фазы; иначе `null`.

- [ ] **Step 7:** `uv run pytest -q tests/test_project_logic.py tests/test_project_core.py tests/test_prompt_config.py && uv run pytest -q` — PASS.

- [ ] **Step 8: Коммит** — `Срез 102: итог этапа от наставника, страж чисел, «Подтвердить и дальше»`.

---

### Task 2: API и вид проекта (срез 103)

**Files:**
- Modify: `src/llm_tutor/web/api_project.py`, `src/llm_tutor/web/views.py`
- Test: `tests/test_web_project.py`

**Interfaces:**
- Consumes: Task 1 (`accept_stage`, `clean_proposal(..., stage=)`).
- Produces (API): `POST /api/courses/{id}/project/accept`, тело `{"turn_id": int}` (`TakeBody`), ответ `{"project": view}`; ошибки — через `PROJECT_ERRORS` (`not_found` 404, `wrong_status`/`wrong_stage` 409, `missing` 422 с `missing`).
- Produces (вид `views.project_view`): у реплики наставника `proposal: null | {"kind": "card" | "stage", "stage": str | None, "title": str, "fields": [{"key", "label", "value", "required"}], "actionable": bool}`; `title` — «Предложение для карточки» / «Итог этапа «<название>»»; на верхнем уровне `quick_reply: str | None` (тексты — Global Constraints).

- [ ] **Step 1: Падающие тесты** — `tests/test_web_project.py` (фикстуры `ready`, `web_app`, хелперы `_pass_course`, `_start`; `web_llm` переопределить в тесте через `ProjectLLM` с `proposal` этапа):
  - `test_accept_flow` — `build`, реплика с предложением обоих обязательных `data` → `POST …/accept` 200, `project.stage == "baseline"`, в `messages` у этой реплики `proposal.actionable is False`.
  - `test_accept_errors` — неизвестный `turn_id` → 404; повторный `accept` той же реплики → 409; предложение без обязательного → 422 и `detail.missing == ["findings"]`, этап не сменился.
  - `test_proposal_view_shape` — в `idea` `proposal.kind == "card"`, `fields[0] == {"key": "title", "label": "Название проекта", "value": …, "required": True}`, `actionable is True`; после `approve` та же реплика — `actionable is False`.
  - `test_quick_reply` — `idea` → «Предложи формулировки карточки», `build` → «Подвести итог этапа», `done` → `None`.
  - `test_accept_under_lock` — по образцу существующего теста замка: ход `accept` ждёт `pool.lock`.

- [ ] **Step 2:** `uv run pytest -q tests/test_web_project.py` — FAIL.

- [ ] **Step 3: Реализовать** маршрут `project_accept` рядом с `project_take` и вид предложения в `views.project_view` (`kind` по наличию `meta.stage`; `actionable` — по спеке «Вид проекта»).

- [ ] **Step 4:** `uv run pytest -q tests/test_web_project.py && uv run pytest -q` — PASS.

- [ ] **Step 5: Коммит** — `Срез 103: API «Подтвердить и дальше», предложение объектом, быстрая кнопка`.

---

### Task 3: Страница — чат слева, сводка справа (срез 104)

**Files:**
- Modify: `src/llm_tutor/web/templates/project.html`, `src/llm_tutor/web/static/js/project.js`, `src/llm_tutor/web/static/css/project.css`, `src/llm_tutor/web/static/js/split.js` (только если нужна новая пара панелей)
- Test: `tests/test_web_project_pages.py`

**Interfaces:**
- Consumes: Task 2 (вид проекта, `POST …/accept`, существующие `PUT card`, `PUT notes`, `POST complete`, `POST approve`, `POST take`).
- Produces (разметка): `#project-steps` — полоса этапов (`<ol>`, у шагов `data-status="done|current|ahead"`, `aria-current="step"` у текущего); `#pane-chat` — слева, `#pane-summary` — справа; окна `<dialog id="card-dialog">` и `<dialog id="stage-dialog">`; быстрая кнопка `#quick-reply`; в предложении кнопки `[data-action="take"|"accept"|"edit"]`.

- [ ] **Step 1: Падающие тесты страниц** — `tests/test_web_project_pages.py`:
  - `test_project_page_layout` — в HTML `/project/mlcourse` (проект начат) есть `id="project-steps"`, `id="pane-chat"`, `id="pane-summary"`, `id="card-dialog"`, `id="stage-dialog"`, `id="quick-reply"`; `pane-chat` идёт в разметке раньше `pane-summary`.
  - существующие проверки «нет inline-скриптов и `style=`» распространить на новую разметку.

- [ ] **Step 2:** `uv run pytest -q tests/test_web_project_pages.py` — FAIL.

- [ ] **Step 3: Реализовать** по спеке («Что видит ученик»):
  - шаблон: шапка с полосой этапов; `split` — чат слева, сводка справа; оба окна; разделитель — прежний `split.js` с ширинами 62/38;
  - `project.js`: из левой части убрать формы карточки и заметок; сводка — карточка текстом + «Изменить» (окно, `PUT card`) и «Утвердить идею» в `idea`; текущий этап — цель, чек-лист, «Записать итог самому»; пройденные — `<details>` с итогом; `done` — итог всех этапов;
  - предложение в реплике: заголовок, поля «подпись — значение» (`textContent`), кнопки по `kind` и `actionable`; «Подтвердить и дальше» → `POST accept`, при 422 — открыть `stage-dialog` с полями предложения и подсветкой `missing`; «Поправить» → `stage-dialog` с полями предложения; «Сохранить и дальше» в окне → `PUT notes` → `POST complete` (422 — подсветка, окно остаётся);
  - быстрая кнопка шлёт `quick_reply` обычной репликой; в `done` скрыта;
  - телефон: переключатель «Наставник / Проект», по умолчанию «Наставник».

- [ ] **Step 4:** `uv run pytest -q tests/test_web_project_pages.py && uv run pytest -q` — PASS; глазами — страница на демо-данных (`scripts/demo_students.py`, `showcase.py close-module 1`) в статусах `idea`, `build`, `done`, ширина 1440 и 400 px.

- [ ] **Step 5: Коммит** — `Срез 104: страница проекта — разговор слева, сводка и этапы справа`.

---

### Task 4: E2E и документы (срез 105)

**Files:**
- Modify: `tests/e2e/test_project.py`, `tests/e2e/helpers.py`, `CLAUDE.md`, `README.md`, `llm-tutor-architecture.md` (параграф проекта), `docs/demo/SHOWCASE.md` (шаг 9), `docs/superpowers/specs/2026-10-27-course-project-design.md` (ссылка на эту спеку)
- Test: `tests/e2e/test_project.py`

**Interfaces:**
- Consumes: Tasks 1–3.

- [ ] **Step 1: E2E** — переписать `test_project_flow` под новый поток и добавить:
  - `test_project_flow` — выбор описания → реплика → «Взять в карточку» → «Утвердить идею» → реплика с предложением итога `data` → «Подтвердить и дальше» → в полосе этапов «Базовая модель» текущая, итог «Данные и анализ» в `<details>`.
  - `test_accept_missing_opens_dialog` — предложение без `findings` → «Подтвердить и дальше» → открыт `stage-dialog`, поле `findings` подсвечено → ввести текст → «Сохранить и дальше» → этап сменился.
  - `test_manual_stage_without_model` — модель падает (`ProjectLLM(fail=True)`) → «Записать итог самому» → заполнить обязательные → «Сохранить и дальше» → этап сменился.
  - `test_project_narrow_screen_switch` — по умолчанию виден чат, переключатель показывает сводку.

  Run: `uv run pytest -q -m e2e tests/e2e/test_project.py` — PASS.

- [ ] **Step 2: Документы** — `CLAUDE.md`, подводный камень «Проект по курсу»: итог этапа предлагает наставник (`proposal` в `build`, без `code`), страж чисел в `clean_proposal`, «Подтвердить и дальше» — `accept_stage` одним коммитом, значения из сохранённой реплики; таблица модулей — `api_project` с `accept`. README и архитектура — по абзацу. Шаг 9 `SHOWCASE.md` — новый поток (реплика → предложение итога → «Подтвердить и дальше»).

- [ ] **Step 3:** `uv run pytest -q` — PASS.

- [ ] **Step 4: Коммит** — `Срез 105: E2E и документы проекта «разговор вместо форм»`.
