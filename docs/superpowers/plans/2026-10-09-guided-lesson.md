# Срез 28 «Урок за ручку» — план (MVP на 40 минут)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Шаги — чекбоксы.

**Goal:** бот объясняет тему по частям с кнопкой «Дальше ▶️», задаёт один вопрос-проверку
(два шанса), а посторонний текст при висящем задании не засчитывает как неверный ответ.

**Architecture:** новый модуль `core/lesson.py` (чистые функции: части урока, «ответ или
реплика»); `core/turn.py` только проводка; колбэк «Дальше» в `bot/handlers.py`. Урок — один
вызов тьютора, части разделены строкой `---` и лежат в `SessionState`.

**Tech Stack:** Python 3.12, aiogram 3, pydantic 2, pytest (asyncio_mode=auto).

**Spec:** нет — по решению пользователя. Дизайн утверждён в чате 2026-10-09 (части 1–3);
отличия MVP от него — в разделе «Отложено».

## Global Constraints

- Комментарии, докстринги, коммиты — по-русски; коммит `Срез 28: …`, в конце `Co-Authored-By`.
- `core/*` не импортирует `bot/*`; `core` отдаёт сырой текст, хендлер экранирует (`render.fit(render.escape(...))`).
- Новые поля состояния — только в `SessionState` с дефолтами (без миграций).
- TDD: тест → красный → код → зелёный → `uv run pytest -q` целиком.
- Многострочные правки .py — инструментом Edit.

## Review Focus

1. Ученик пишет «а что такое индекс» (без «?») при висящем вопросе с вариантами → ответ тьютора, провала в журнале нет, задание висит.
2. Повторное/старое нажатие «Дальше» → «Эта часть уже позади», состояние не меняется.
3. Модель вернула урок без `---` → одна часть + «Проверим ✅», урок не ломается.
4. Две ошибки подряд → тема закрыта, бот ведёт к следующей, не зацикливается на вопросах.
5. Кнопка с текстом-числом («150») засчитывается верно (F-1).

---

### Task 0: F-1 — верная кнопка-число засчитывается как ошибка (~5 мин)

**Files:** Modify `src/llm_tutor/grader/autocheck.py:46-54`, `src/llm_tutor/core/turn.py:118-131`; Test `tests/test_course_data.py`.

- [ ] Тест: для каждого `choice` в курсе `autocheck.check(item, item.options[int(item.answer)]).score == 1.0`, для остальных вариантов `0.0`; плюс `turn._normalize_choice_answer(item, opt) == opt` для каждого варианта.
- [ ] Красный (3043: «150»).
- [ ] Фикс: в `_option_text` сначала искать `candidate` среди нормализованных текстов вариантов и только потом трактовать цифры как индекс; в `_normalize_choice_answer` — если текст совпадает с вариантом, вернуть его как есть.
- [ ] `uv run pytest -q`; коммит `Аудит финального ревью: вариант-число засчитывается верно (F-1)`.

### Task 1: «Ответ или реплика» (~7 мин)

**Files:** Create `src/llm_tutor/core/lesson.py`; Modify `src/llm_tutor/core/turn.py` (`_looks_like_question` → `lesson.answer_kind`, строки 134-141, 529, 683); Test `tests/test_lesson.py`.

**Produces:** `answer_kind(item: Item, text: str) -> Literal["answer", "chat"]`.

```python
_QUESTION_WORDS = ("что", "как", "почему", "зачем", "а ", "какой", "какая", "какие", "где", "когда", "объясни", "не понял")
SHORT_ANSWER_MAX_WORDS = 3

def answer_kind(item: Item, text: str) -> Literal["answer", "chat"]:
    """Ответ на висящее задание или реплика тьютору (посторонний вопрос)."""
    stripped = text.strip()
    lowered = stripped.lower()
    if item.answer_type == "choice":
        options = {autocheck.normalize_answer(o) for o in item.options}
        is_number = stripped.isdigit() and 1 <= int(stripped) <= len(item.options)
        return "answer" if is_number or autocheck.normalize_answer(stripped) in options else "chat"
    if item.answer_type == "short":
        looks_question = stripped.endswith("?") or lowered.startswith(_QUESTION_WORDS)
        short = len(stripped.split()) <= SHORT_ANSWER_MAX_WORDS
        return "answer" if short and not looks_question else "chat"
    return "chat" if stripped.endswith("?") else "answer"  # open/code — как раньше
```

- [ ] Тесты (параметризованные): choice — «2», текст варианта → answer; «а что такое индекс», «подожди» → chat. short — `max_depth`, `0.25` → answer; «зачем это нужно», длинная фраза → chat. Интеграционный: `handle_turn` с висящим choice и текстом «а что такое индекс» → событий-провалов нет, `pending_item_id` не снят.
- [ ] Красный → реализация → проводка в `handle_turn` (условие ветки ответа: `lesson.answer_kind(item, user_text) == "answer"`) → зелёный.
- [ ] Коммит `Срез 28: посторонний текст при задании — реплика, а не неверный ответ`.

### Task 2: Урок по частям и «Дальше ▶️» (~15 мин)

**Files:** Modify `src/llm_tutor/schemas.py` (`SessionState`), `src/llm_tutor/core/lesson.py`, `src/llm_tutor/core/turn.py` (вход в узел 627-709, `TurnReply`), `src/llm_tutor/bot/handlers.py`; Test `tests/test_lesson.py`, `tests/test_bot_flows.py`.

**Produces:**
- `SessionState.lesson_parts: list[str] = []`, `lesson_part: int = 0`.
- `lesson.split_parts(text: str) -> list[str]` — режет по строке `---`, не больше 3 частей, пустые отбрасывает, без разделителя — `[text]`.
- `lesson.LESSON_KICKOFF = "Объясни тему «{name}» по шагам: идея → пример кода → где это применяют. Каждый шаг 2–4 предложения; раздели шаги строкой ---. Вопросов не задавай."`
- `TurnReply.button: tuple[str, str] | None` — (подпись, callback_data): `("Дальше ▶️", "lesson:next:<node>:<k>")` или `("Проверим ✅", ...)` на последней части.
- `turn.lesson_next_reply(conn, node_id: str, part: int, *, now=None, settings=None) -> TurnReply` — без модели.
- `turn._start_lesson(conn, client, model, session_id, state, graph, node_id, *, now, settings) -> tuple[str, SessionState, tuple[str, str]]` — вход в узел: вызов тьютора с `LESSON_KICKOFF`, части в состояние, возвращает первую часть и её кнопку (используется и в Task 3 после закрытия темы).

- [ ] Тесты: `split_parts` (3 части; без `---` → 1; пустые куски отброшены). Вход в узел с фейковым тьютором, отвечающим «A\n---\nB\n---\nC» → ответ «A» + кнопка `lesson:next:<node>:1`, задания нет. `lesson_next_reply(node, 1)` → «B»; на последней → кнопка «Проверим ✅»; нажатие «Проверим» → задание с вариантами. Старый `part` или чужой `node` → «Эта часть уже позади», состояние не изменилось. Свободный текст без задания → ответ тьютора + кнопка «Дальше» текущей части, задания нет.
- [ ] Красный.
- [ ] Реализация: при входе в узел `_tutor_branch` зовётся с `user_text=LESSON_KICKOFF.format(name=...)`; `parts = split_parts(reply)`; в состояние `lesson_parts=parts, lesson_part=1`; `_issue_task` на входе НЕ зовётся; ветка `moves_lesson` (680-705) больше не выдаёт задание — вместо этого возвращает кнопку следующей части. `lesson_next_reply`: проверка `node_id == state.current_node_id and part == state.lesson_part`; если частей больше — показать следующую, иначе `_issue_task`. Сбой модели (`LLM_FAILURE_REPLY`) → часть = `graph.concept(node).description`, кнопка «Проверим ✅». Хендлер `@router.callback_query(F.data.startswith("lesson:next:"))` → `lesson_next_reply` → `_send_reply`; `_send_reply` добавляет кнопку `reply.button`, если нет вариантов. В FSM (анкета) — `BUSY_REPLY`.
- [ ] Зелёный → `uv run pytest -q` (старые тесты «после объяснения сразу задание» переписать под кнопку — список в коммите) → коммит `Срез 28: урок по частям с кнопкой «Дальше»`.

### Task 3: Один вопрос, два шанса (~10 мин)

**Files:** Modify `src/llm_tutor/config.py:102` (`guide_success_streak` default 2 → 1), `src/llm_tutor/schemas.py` (`check_misses: int = 0`), `src/llm_tutor/core/turn.py` (ветка ответа 531-625, `_close_node_if_ready` — параметр `force: bool = False`); Test `tests/test_lesson.py`.

- [ ] Тесты (урок, не `verify`): верный ответ → «✅ Тема закрыта» + первая часть следующей темы с кнопкой «Дальше». Первая ошибка → фидбек с верным ответом + разбор тьютора + задание с ДРУГИМ `item.id`, `check_misses == 1`. Вторая ошибка → фидбек + разбор + «Тема пока слабое место — вернёмся к ней позже.» + первая часть следующей темы; тема в маршруте `closed`.
- [ ] Красный.
- [ ] Реализация: при неверном ответе в уроке — разбор через `_tutor_branch(user_text=f"Я ответил «{answer}», а верно «{correct}». Объясни коротко, почему.", allow_stuck=False)`; `check_misses == 0` → `check_misses=1` и `_issue_task(exclude_item_ids={answered})`; `check_misses == 1` → `_close_node_if_ready(..., force=True)` (пропускает `guide.is_node_closed`), затем вход в следующий узел как в Task 2. После закрытия (верно или force) вместо `_tutor_branch`+`_issue_task` — старт урока следующей темы (общая функция `_start_lesson` из Task 2). `check_misses` сбрасывается при смене узла.
- [ ] Зелёный → `uv run pytest -q` → коммит `Срез 28: один вопрос на тему, после второй ошибки — дальше`.

### Task 4: Финал (~5 мин)

- [ ] `uv run pytest -q` — всё зелёное; ручной прогон по `docs/demo-checklist.md` (обновить шаги: «Дальше ▶️», один вопрос).
- [ ] `problems.md`: раздел «Срез 28 — отложено» (список ниже); журнал `progress.md` — Ruling'и.
- [ ] Коммит `Срез 28: чек-лист и отложенное`.

## Отложено (в problems.md, не делаем сейчас)

- Пометка ⚠️ «слабая тема» в `/plan` (флаг `weak` в `RouteStep`).
- Отдельная JSON-схема урока вместо разделителя `---`.
- `guide_success_streak=1` действует и на `/close`: проход «закрой тему» тоже из одного вопроса (дизайн хотел оставить прежний).
- Классификатор «ответ/реплика» моделью для пограничных фраз.
- F-2, F-3 (прыжки между модулями), F-4…F-8 финального ревью.
- Адверсариальное ревью среза 28 отдельным агентом.
