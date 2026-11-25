# Конспект по скелету курса — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** страница «Конспект» (`/notes`) собирается сама по скелету курса: суть каждого пройденного раздела, вопросы тьютору, фрагменты и загвоздки ученика, в виде «чистых заметок».

**Architecture:** сборка при открытии страницы из БД ученика (подход А спеки): чистые функции разбора текста — `web/gist.py`; сборка курса — новый `web/notebook.py` поверх существующего `web/notes.py` (вопросы, решения, заметки к вопросам); шаблон `notes.html` и `notes.css` переделываются под вид B. Миграций нет.

**Tech Stack:** Python 3.12, FastAPI + Jinja2, SQLite, pytest (`asyncio_mode=auto`), JS без сборки.

**Spec:** `docs/superpowers/specs/2026-11-25-notebook-skeleton-design.md`

## Global Constraints

- Слои: `web → core/student/db/course`; `core/*`, `student/*` не импортируют `web/*`.
- Markdown — только через `web.markdown.render`; текст ученика и модели в шаблоне — автоэкранирование Jinja. CSP: без `style=""` и встроенных скриптов.
- Цвета — токены `tokens.css`; тёмные токены правятся парно (`tests/test_audit_48.py`).
- Суть раздела: не больше **~320 знаков** (`GIST_CHARS = 320`), «короткий абзац» — меньше **60 знаков** (`SHORT_PARAGRAPH = 60`).
- Ошибка в задании — `result < diagnostic.SUCCESS_SCORE`, источник не `explain`; свёртка событий по `(item_id, ts)`.
- Тесты конспекта — `tests/test_notebook.py` (+ новый `tests/test_notebook_gist.py`); фрагменты golos — `tests/test_notes.py`. Тесты не зависят от `.env`.
- Коммиты по-русски: `Срез N: …`, аудит — `Аудит среза N: …` (срезы 106–112). Каждый срез — отдельное адверсариальное ревью агентом, находки — отдельным коммитом с регрессионным тестом.
- Тесты запускать: `uv run python -m pytest -q -p no:cacheprovider <файлы>` (на этом ноутбуке `pytest.exe` блокирует Smart App Control).

## Review Focus

1. **Конспект занятия без раздела `## <название>`** (раздел переименовали, конспекта нет, курс из админки без конспекта) — раздел показывается с заголовком, без сути, страница не падает. → тест в Task 1 и Task 2.
2. **Событие по заданию, которого больше нет** (`repos.get_item` → `None`, задание погашено, занятие ушло из графа) — загвоздка пропускается или уходит в «Вне занятия», без 500. → тест в Task 5.
3. **Фрагмент с `section_id` чужого/удалённого раздела или занятия вне графа** — уходит на уровень занятия или в «Вне занятия». → тест в Task 4.
4. **Ученик записан, но без анкеты** (нет сессии или `state.route is None`) — весь курс «впереди», суть не показывается, 200. → тест в Task 2.
5. **HTML/скрипт в итоге тьютора, цитате, комментарии, ответе ученика** — экранируется (итог и суть — через `render`, остальное — текстом). → тест в Task 3 и Task 4.

---

### Task 1 (Срез 106): суть раздела из конспекта курса

**Files:**
- Create: `src/llm_tutor/web/gist.py`
- Test: `tests/test_notebook_gist.py`

**Interfaces:**
- Produces:
  - `GIST_CHARS = 320`, `SHORT_PARAGRAPH = 60`
  - `section_gist(section_md: str, *, limit: int = GIST_CHARS) -> str | None` — вход: текст раздела как отдаёт `course.seed.section_text` (начинается с `## …`); выход — Markdown сути или `None`.
  - `answer_gist(markdown: str, *, limit: int = GIST_CHARS) -> str | None` — первый обычный абзац ответа тьютора, обрезанный тем же правилом (без логики `### Главное` и двоеточий).
  - `clip_sentences(text: str, limit: int) -> str`

- [ ] **Step 1: Тесты `tests/test_notebook_gist.py`**

```python
def test_main_subsection_first_paragraph():
    md = "## Энтропия\n\nВводный абзац.\n\n### Главное\n\n**Энтропия** — мера неопределённости. Второе предложение.\n\n### Пример\n\n```python\nx = 1\n```"
    assert section_gist(md) == "**Энтропия** — мера неопределённости. Второе предложение."

def test_without_main_takes_first_paragraph():          # курсы из админки
    assert section_gist("## Протоколы\n\nПротокол — набор методов.\n\nЕщё абзац.") == "Протокол — набор методов. Ещё абзац."
    # (первый абзац короче SHORT_PARAGRAPH → добавлен следующий)

def test_colon_then_list_inlines_items():
    md = "## Groupby\n\n### Главное\n\n`df.groupby(\"k\")` работает в три шага:\n\n- **разбиение** на группы;\n- **применение** функции;\n- **объединение** результатов.\n"
    assert section_gist(md) == "`df.groupby(\"k\")` работает в три шага: **разбиение** на группы; **применение** функции; **объединение** результатов."

def test_colon_then_code_takes_next_paragraph():
    md = "## Осмотр\n\n### Главное\n\nПять вызовов:\n\n```python\ndf.head()\n```\n\nОсмотр начинают с размера и типов столбцов, потом смотрят пропуски."
    assert section_gist(md) == "Осмотр начинают с размера и типов столбцов, потом смотрят пропуски."

def test_clip_keeps_whole_sentences_under_limit():
    text = "Первое предложение про узлы. " * 20
    out = clip_sentences(text.strip(), 120)
    assert len(out) <= 120 and out.endswith(".")

def test_long_single_sentence_clipped_by_word_with_ellipsis():
    out = clip_sentences("слово " * 100, 50)
    assert out.endswith("…") and len(out) <= 51 and "  " not in out

def test_clip_does_not_break_bold_or_code():
    text = "Начало. " + "**жирный термин который длинный** " * 10
    out = clip_sentences(text, 60)
    assert out.count("**") % 2 == 0 and out.count("`") % 2 == 0

def test_decimal_and_abbreviation_not_sentence_end():
    assert clip_sentences("Максимум 0,5 при равных долях, т. е. классы поровну. Хвост длинного второго предложения.", 60) == "Максимум 0,5 при равных долях, т. е. классы поровну."

def test_only_code_or_missing_gives_none():
    assert section_gist("## Код\n\n```python\nx = 1\n```") is None
    assert section_gist("") is None

def test_answer_gist_first_paragraph():
    assert answer_gist("Джини без логарифма.\n\n```python\nx\n```\n\nЕщё.") == "Джини без логарифма."

def test_every_real_section_has_short_gist():            # данные курсов
    # data/theory/*/*.md и data/demo/theory/*/*.md: для каждого раздела seed,
    # у которого есть `### Главное`, section_gist не None, len <= GIST_CHARS + 1,
    # не кончается на ":" и чётное число "**" и "`".
```

- [ ] **Step 2: Запустить — падают** (`ModuleNotFoundError: llm_tutor.web.gist`).
  Run: `uv run python -m pytest -q -p no:cacheprovider tests/test_notebook_gist.py`
- [ ] **Step 3: Реализовать `web/gist.py`.** Абзац — подряд идущие строки обычного текста; пропуск: ограждения ```` ``` ````/`~~~` (до закрывающего), строки списков (`- `, `* `, `1. `), таблиц (`|`), цитат (`>`), заголовков (`#`). Пункт списка для вставки — текст пункта до конца первого предложения, Markdown пункта цел; пункты добавляются, пока суть ≤ `limit`. Предложения режутся по `[.!?]` + пробел + заглавная/кавычка/конец, но не после одно-/двухбуквенного сокращения («т. е.», «см.») и не внутри `**…**`/`` `…` ``; «0,5» — не граница. Строки абзаца склеиваются пробелом.
- [ ] **Step 4: Запустить — зелёные.** Тот же Run → `passed`.
- [ ] **Step 5: Коммит** `Срез 106: суть раздела конспекта из «Главного» курса (web/gist)`.

---

### Task 2 (Срез 107): скелет курса на странице «Конспект»

**Files:**
- Create: `src/llm_tutor/web/notebook.py`
- Modify: `src/llm_tutor/web/pages.py` (`notes_page`, импорт `web.notes` переименовать в `notes_feed`), `src/llm_tutor/web/templates/notes.html`, `src/llm_tutor/web/static/css/notes.css`
- Test: `tests/test_notebook.py`

**Interfaces:**
- Consumes: `gist.section_gist`; `course.seed.section_text`; `repos.get_theory`, `repos.get_topics`, `repos.get_open_session`, `repos.get_session_state`; `CourseGraph.load(...).topo_order()`; `student.explain.pending_lesson(conn, route, order)`.
- Produces (словари для Jinja; ключи — контракт для Task 3–7):
  - `course_notebook(conn, *, skeleton: bool = True, query: str | None = None, node_id: str | None = None) -> dict` →
    `{"lessons": [Lesson], "ahead": [str], "outside": Lesson | None, "count": int, "closed": int, "total": int}`
  - `Lesson = {"node_id", "name", "module", "status", "locked": bool, "sections": [Section], "questions": [], "fragments": [], "mistakes": [], "explain": [], "count": int}`; `status ∈ {"passed", "weak", "reopened", "current", "ahead"}`
  - `Section = {"id", "title", "passed": bool, "gist_html": Markup | None, "questions": [], "fragments": [], "mistakes": []}`
  - `lesson_statuses(route: Route | None, current: str | None) -> dict[str, str]`
  - `STATUS_LABELS = {"passed": "пройдено", "weak": "пройдено с ошибками", "reopened": "открыто снова", "current": "идёт сейчас", "ahead": "впереди"}`
  - `LOCKED_NOTE = "Откроется после объяснения новичку."`
- Страница: `?view=notebook` (по умолчанию; `questions` — то же), `?view=solutions` — без изменений. Курс: `?course=<id>`; без него — первый видимый записанный курс (как `pages._current_card`); `?all=1` — все курсы, `skeleton=False`.

- [ ] **Step 1: Тесты.** Хелпер в тесте `_route(conn, *, closed=(), weak=(), reopened=(), current=None, passed_sections=(), section=None)` пишет `SessionState` с `Route` в открытую сессию (`repos.ensure_open_session`, `repos.update_session_state`). Тесты (`enrolled`, `web_app`, `_user_db` — как сейчас в файле):

```python
async def test_skeleton_lists_lessons_in_course_order_with_sections(enrolled, web_app): ...
    # все занятия опубликованных модулей курса в порядке topo_order; у закрытого —
    # заголовки всех его разделов (h4) и суть; у остальных — в строке «Впереди: …»
async def test_passed_section_has_gist_ahead_section_has_line(enrolled, web_app): ...
    # текущее занятие: passed_sections=[s1], section=s2 → у s1 и s2 суть, у s3 строка
    # «<название s3> — ещё впереди» и нет текста его «Главного»
async def test_reopened_lesson_keeps_gist(enrolled, web_app): ...
    # шаг status="ahead" c closed_at → статус «открыто снова», суть разделов есть
async def test_weak_lesson_label(enrolled, web_app): ...          # «пройдено с ошибками»
async def test_pending_explain_lesson_is_locked(enrolled, web_app): ...
    # важное занятие закрыто после explain_since без сессии → в HTML LOCKED_NOTE,
    # текста «Главного» его разделов нет
async def test_no_route_everything_ahead(enrolled): ...           # Review Focus 4
    # без анкеты: 200, «Впереди: …» со всеми занятиями, суть не показана
async def test_section_without_theory_heading(enrolled, web_app): ...   # Review Focus 1
    # repos theory занятия без нужного "## …" → раздел с заголовком, без сути, 200
async def test_default_course_is_current_and_all_has_no_skeleton(...): ...
    # два курса: без ?course= выбран текущий (первый видимый записанный);
    # ?all=1 — нет строк «ещё впереди» и «Впереди:»
async def test_old_view_questions_is_notebook(enrolled): ...      # ?view=questions == по умолчанию
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализовать `web/notebook.py` и страницу.** Пройденность: занятие с `closed_at` или `status == "closed"` → все разделы; текущее → `passed_sections ∪ weak_sections ∪ {section_id}`. `locked` — `node_id == pending_lesson(...)`. Занятия «впереди» без материала → имя в `ahead`; при `skeleton=False` и в режиме поиска занятия без материала не выводятся. Вид B (`.superpowers/brainstorm/notebook-look.html`, вариант B): занятие — `h3` с меткой статуса, раздел — `h4`, суть — абзац `p.gist` (термины `strong` подсвечены токеном), «впереди» — бледные строки. Вкладки: «Конспект» | «Выполненные задания».
- [ ] **Step 4: Запустить `tests/test_notebook.py` и `tests/test_notes.py` — зелёные.** Старые тесты ленты «Определения и пояснения», которые проверяли вид, поправить под новый вид; поведение (отбор вопросов, поиск, заметка) не менять.
- [ ] **Step 5: Коммит** `Срез 107: скелет курса на странице «Конспект» (суть, статусы, закрытое занятие)`.

---

### Task 3 (Срез 108): вопросы тьютору в разделах

**Files:** Modify `src/llm_tutor/web/notes.py` (`question_entries`, `_entry`), `src/llm_tutor/web/notebook.py`, `notes.html`, `notes.css`; Test `tests/test_notebook.py`.

**Interfaces:**
- Consumes: `notes.question_entries` (запрос расширить `a.meta AS answer_meta`), `gist.answer_gist`.
- Produces: в записи вопроса новый ключ `"gist_html": Markup | None` = `render(meta.takeaway)` ответа, иначе `render(answer_gist(answer))`; раскладка в `Section["questions"]` по `meta.section`, без раздела — `Lesson["questions"]`, занятие вне графа — `outside`.

- [ ] **Step 1: Тесты.**

```python
async def test_question_shows_takeaway_as_gist(enrolled, web_app): ...
    # _add c ответом и assistant meta {"takeaway": "Джини без логарифма."} →
    # в разделе вопрос и «Джини без логарифма.»; полный ответ под <details>
async def test_question_without_takeaway_uses_first_paragraph(...): ...
async def test_question_without_section_goes_to_lesson_block(...): ...
async def test_takeaway_html_is_escaped(...): ...           # Review Focus 5: "<script>" не исполняется
async def test_failed_answer_is_muted_line(...): ...        # «ответ не получен»
async def test_personal_note_still_editable(...): ...       # ссылка «заметка», PUT …/notebook/{id} работает
```
  `_add` в тесте получает параметр `takeaway: str | None` и пишет его в `meta` реплики `assistant`.
- [ ] **Step 2–4:** падают → реализовать → зелёные (`tests/test_notebook.py`).
- [ ] **Step 5: Коммит** `Срез 108: вопросы тьютору в разделах конспекта с итогом тьютора`.

---

### Task 4 (Срез 109): фрагменты в конспекте, профиль без списка

**Files:** Modify `src/llm_tutor/web/notebook.py`, `src/llm_tutor/db/repos.py` (`list_notes(conn, *, limit: int | None = NOTE_LIMIT)` — `None` без лимита), `notes.html`, `notes.css`, `src/llm_tutor/web/pages.py` (`profile`), `templates/profile.html`, `static/js/notes.js` (текст подтверждения удаления: «из конспекта»); Test `tests/test_notebook.py`, `tests/test_notes.py`.

**Interfaces:**
- Produces: `Section["fragments"]` / `Lesson["fragments"]` / `outside["fragments"]`, запись `{"id", "quote", "comment", "source": SOURCE_LABELS-подпись, "when"}`; разметка совместима с `notes.js`: корень `li.note-row`, форма `.note-comment-form[data-note][data-course]`, кнопка `[data-delete-note][data-course]`, текст `.note-comment`.
- Профиль: нет списка заметок и их поиска; строка «Заметки и сохранённые фрагменты — в конспекте» со ссылкой `/notes`; поиск по материалам курса (`search_course`) остаётся.

- [ ] **Step 1: Тесты.**

```python
async def test_fragment_in_its_section_with_comment(...): ...
async def test_fragment_lesson_level_and_outside(...): ...   # Review Focus 3: чужой section_id, concept вне графа
async def test_all_fragments_shown_beyond_100(...): ...       # 101 фрагмент → все в конспекте
async def test_fragment_quote_escaped(...): ...               # Review Focus 5
async def test_profile_links_to_notebook_instead_of_list(...): ...
```
  В `tests/test_notes.py` тесты профиля (`test_note_api_and_profile_search`, `test_note_comment_and_search_by_it`) переписать: заметка видна в `/notes`, а не в `/profile`.
- [ ] **Step 2–4:** падают → реализовать → зелёные (`tests/test_notebook.py tests/test_notes.py tests/test_web_admin.py`).
- [ ] **Step 5: Коммит** `Срез 109: фрагменты в конспекте, в профиле — ссылка на конспект`.

---

### Task 5 (Срез 110): загвоздки — ошибки в заданиях

**Files:** Modify `src/llm_tutor/web/notebook.py`, `notes.html`, `notes.css`; Test `tests/test_notebook.py`.

**Interfaces:**
- Produces: `task_mistakes(conn) -> list[dict]` — запись `{"item_id", "node_id", "section_id": str | None, "final": bool, "prompt": str, "given": str, "correct": str | None, "solved_later": bool, "ts"}`; раскладка — `Section["mistakes"]` (по `item.section`), итоговое (`final`) — `Lesson["mistakes"]`. Длины: `prompt` ~120 знаков (первое предложение/строка), `given` ~160, `correct` ~200 (через `clip_sentences`). `given` для кода — «код не прошёл проверку»; `correct` для `choice` — текст верного варианта (`options[int(answer)]`), для открытого — `item.answer`, для кода — `None`.

- [ ] **Step 1: Тесты** (хелпер `_solution` в файле уже пишет ответ и событие одним `ts`):

```python
async def test_wrong_choice_shows_given_and_correct(...): ...
async def test_mistake_later_solved_marked(...): ...          # потом result=1.0 → «потом решено верно»
async def test_one_entry_per_item_latest_wrong(...): ...      # два неверных + несколько концептов → одна запись
async def test_final_item_mistake_on_lesson_level(...): ...
async def test_code_mistake_without_reference(...): ...
async def test_explain_events_are_not_mistakes(...): ...      # source="explain" не попадает
async def test_missing_item_event_skipped(...): ...           # Review Focus 2: item_id без строки items → 200
```
- [ ] **Step 2–4:** падают → реализовать (SQL: `events` с `item_id IS NOT NULL AND source != 'explain'`, свёртка `MIN(id)` по `(item_id, ts)`, ответ — реплика `user` с тем же `ts`, как `notes.solution_entries`) → зелёные.
- [ ] **Step 5: Коммит** `Срез 110: загвоздки конспекта — ошибки в заданиях`.

---

### Task 6 (Срез 111): загвоздки — «Объясни новичку»

**Files:** Modify `src/llm_tutor/web/notebook.py`, `notes.html`, `notes.css`; Test `tests/test_notebook.py`.

**Interfaces:**
- Consumes: `repos.list_explain_sessions`, `repos.get_explain_configs`.
- Produces: `explain_snags(conn) -> dict[str, list[dict]]` — занятие → записи `{"kind": "wrong" | "missed" | "pitfall", "text": str, "belief": str | None}`; подписи: `"wrong"` — «Ошибся, объясняя:», `"missed"` — «Упустил:», `"pitfall"` — «Не заметил ловушку:». Кладутся в `Lesson["explain"]`.
- Правила (из спеки): по каждому занятию — последняя завершённая сессия, где оно встречается в отчёте (`IdeaReport.lesson`/`PitfallReport.lesson`); `missed` — только обязательные по текущему эталону; идеи вне эталона пропускаются; `belief` — из эталона по id загвоздки.

- [ ] **Step 1: Тесты** (сессию создать `repos.create_explain_session` + `repos.update_explain_session` с `status="finished"` и `report`):

```python
async def test_explain_wrong_idea_shown(...): ...
async def test_explain_missing_required_only(...): ...        # необязательная missing не показана
async def test_explain_missed_pitfall_with_belief(...): ...
async def test_explain_latest_session_wins(...): ...
async def test_old_module_session_split_by_idea_lesson(...): ...   # concept_id=None
async def test_active_session_ignored(...): ...
```
- [ ] **Step 2–4:** падают → реализовать → зелёные.
- [ ] **Step 5: Коммит** `Срез 111: загвоздки конспекта — промахи «Объясни новичку»`.

---

### Task 7 (Срез 112): поиск, темы, скорость, документы

**Files:** Modify `src/llm_tutor/web/notebook.py`, `src/llm_tutor/web/pages.py`, `notes.html`, `CLAUDE.md` (раздел «Личный конспект» и таблица «Где что»: `web/gist`, `web/notebook`); Test `tests/test_notebook.py`.

**Interfaces:**
- `course_notebook(query=…)`: совпадение по сути, вопросу (вопрос, фрагмент, итог, полный ответ, заметка), фрагменту (цитата, комментарий), ошибке (условие, ответ, верно), промаху объяснения — `notes._matches`. В режиме поиска — только совпавшее, без «ещё впереди» и «Впереди:».
- Чипы «Тема»: занятия курса в порядке `topo_order`, где есть суть или материал; счётчик — материал ученика (`Lesson["count"]`).

- [ ] **Step 1: Тесты.**

```python
async def test_search_finds_gist_question_fragment_mistake(...): ...
async def test_search_hides_skeleton_lines(...): ...
async def test_lesson_filter_shows_one_lesson(...): ...
async def test_lesson_chips_in_course_order_with_counts(...): ...
def test_course_notebook_is_fast_on_full_course(...): ...
    # встроенный курс целиком (33 занятия), 300 вопросов, 100 фрагментов, 200 событий:
    # course_notebook(conn) < 0.1 c (time.perf_counter, лучший из 3)
```
- [ ] **Step 2–4:** падают → реализовать → зелёные; затем весь набор: `uv run python -m pytest -q -p no:cacheprovider` (≈11–13 мин на этом ноутбуке) — без падений.
- [ ] **Step 5: Ручная проверка в браузере:** `uv run python -m llm_tutor.web`, `/notes` под учеником с пройденным занятием — светлая и тёмная тема, ширина 375 px, правка и удаление фрагмента, «заметка» к вопросу.
- [ ] **Step 6: Коммит** `Срез 112: поиск и темы конспекта по скелету, документы`.
