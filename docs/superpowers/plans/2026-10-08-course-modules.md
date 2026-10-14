# Десять модулей курса — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Курс бота растёт с одной темы mlcourse.ai (topic01 «Pandas / EDA») до
всех десяти: у каждого модуля свой граф, банк заданий и анкета на входе,
модули идут по порядку 1 → 10, доказанное знание перестраивает маршрут.

**Architecture:** Один граф на весь курс, модуль — атрибут узла
(`Concept.topic_id`), межмодульные рёбра только назад. Seed лежит по файлу на
модуль (`data/seed_topicNN.json`, с разделом `topic`: название, вводная,
анкета), а в БД все файлы ложатся одним `replace_seed`. Снимок маршрута
покрывает весь курс, но планировщик выбирает тему только внутри **рабочего
участка** текущего модуля (его узлы + незакрытые предки из прошлых модулей).
Анкета `student/survey.py` параметризуется конфигом модуля; вход в модуль с
непройденной анкетой начинается с неё. Контент модулей 2–10 пишется вручную и
проходит адверсариальное ревью.

**Tech Stack:** Python 3.12, uv, aiogram 3, pydantic 2, NetworkX, SQLite + FTS5,
pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-10-08-course-modules-design.md`

## Global Constraints

- Python 3.12, менеджер `uv`. Весь набор: `uv run pytest -q` (на старте 591
  тест, ~10 с). Ветка работы — `course-modules`.
- Стиль: black/isort, type hints на всех сигнатурах, докстринги и комментарии
  по-русски, PEP 8.
- **Конвенция вывода:** `core.*` и тексты `bot/start.py` отдают **сырой** текст —
  хендлер экранирует: `render.fit(render.escape(text))`. `bot/render.*`,
  `themes.switch_node`, `survey.question_view`/`summary_text` отдают **готовый
  HTML** — повторно не экранировать.
- **Слои:** `bot → core/student/db`. `core/*` и `student/*` не импортируют `bot/*`.
- Схема БД меняется только миграцией `migrations/004_topics.sql`. Новые поля
  состояния — в `SessionState`/`Route`/`RouteStep` с дефолтами (старые снимки
  читаются без миграции).
- Подпись `"Уверенно"` в `SELF_LEVELS` и ключи анкеты topic01 (`survey_level`,
  `block_python`, `block_tables`, `block_loading`, `block_selection`,
  `block_analysis`) **не меняются**: на них лежат факты в рабочей БД.
- Для ученика часть курса — **«модуль»**, узел графа — **«тема»**.
- Межмодульные рёбра только назад (`topic(from) ≤ topic(to)`), по умолчанию
  `hard=false`.
- id контента: модуль N — задания с `N·1000`, рубрики и критерии с `N·100`,
  ключи анкеты `tNN_…`.
- Атомарность анкеты: в хендлере `survey:*` до первого сетевого `await` —
  только синхронный код (чтения SQLite допустимы, `await` — нет).
- Тесты асинхронные без декоратора; фейки Telegram — `tests/fakes.py`, хендлер
  по имени — `_named(router, kind, name)`.
- Рабочая БД `data/llm_tutor.sqlite3` до задачи 28 не трогается: CLI в задачах
  гоняется с `--db` во временный файл в scratch-каталоге.
- Пакеты для проверки контента (pandas, scikit-learn и т. п.) — только через
  `uv run --with …`, в `pyproject.toml` не добавляются. `data/raw/` не коммитится.
- Коммиты по-русски: `Срез N: …`, `Аудит среза N: …`, `Модуль N: …`,
  `Аудит модуля N: …`; в конце сообщения строка
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Журнал исполнения: `.superpowers/sdd/2026-10-08-course-modules/progress.md`
  (решения `Ruling:`, находки ревью, отложенное).

## Отступления от спеки (Ruling)

Найдены при чтении кода; спека правится одним коммитом вместе с задачей 4.

1. **Топопорядок** (§3.5): не лексикографический по «порядку в seed» — из БД
   узлы приходят по `ORDER BY id`, порядка seed там нет. Порядок строится
   модуль за модулем, внутри модуля — прежним `nx.topological_sort`. Так
   порядок topic01 гарантированно не сдвигается.
2. **Модуль работы хранится** в `Route.topic_id` (§4.1 говорил «не хранится»):
   иначе незакрытый предок из модуля 1, ставший текущей темой в модуле 2,
   переключал бы весь участок на модуль 1.
3. **`Route.completed_topics`** — модули, о прохождении которых уже сказали
   «🎉»: так возврат к пройденному модулю не празднуется дважды (§4.3).
4. **Путь «назад» через провалы** (§4.2) требует механизма: сейчас закрытая
   тема открывается только просроченным повторением. Добавляется
   `RouteStep.closed_at` и правило: `REOPEN_FAILURES = 2` провала по теме
   **после** закрытия открывают её снова.
5. **Анкета не стирает снимок** (§5.2): `reset_lesson` снимает текущую тему и
   помечает заявленное модуля в снимке, а не обнуляет `route` — иначе анкета
   модуля 2 стёрла бы закрытое в модуле 1. Тема, снятая анкетой с позиции
   «текущая», тоже может стать заявленной (поведение среза 23: `/task` до
   анкеты не отменяет «знакомое»).
6. **Направление межмодульных рёбер** проверяет `CourseGraph` (инвариант
   топопорядка), а не только валидатор seed.
7. **Экраны маршрута** (`/plan`, `/status`, список шагов) переходят на рабочий
   участок в срезе 25, а не 27: снимок на весь курс иначе показал бы сотни шагов.
8. **Прыжок в модуль с непройденной анкетой** делается в срезе 26 в нынешнем
   одноуровневом меню; двухуровневое меню — срез 27.
9. Команды `/task`, `/resume` до анкеты модуля не блокируются (как сейчас в
   модуле 1). Анкета запускается на свободном тексте и сразу после хода,
   который перевёл ученика в новый модуль. Если при этом висит задание
   (ученик успел взять его командой), текст сначала идёт ответом на него, а
   анкета — после хода: иначе ответ терялся бы (ревью плана, M1).
10. Проверка «номер в имени файла» — только для имён вида
    `seed_topicNN*.json` (тесты пишут копии seed как `seed.json`).
11. **Без внешнего ключа** `concepts.topic_id → topics` (§3.2): SQLite не даёт
    `ALTER TABLE … ADD COLUMN … REFERENCES` с ненулевым значением по
    умолчанию. Колонки — `INTEGER NOT NULL DEFAULT 1`, ссылки проверяет
    валидатор курса.
12. **Переоткрытая тема прошлого модуля входит в участок** текущего модуля и без
    ребра к нему (§4.2: провал задания, где тема стоит вторичным весом).
    Признак — `closed_at` у шага, который уже не закрыт: время последнего
    закрытия остаётся меткой «была закрыта, открылась снова» (ревью плана, H1).
13. **`_close_node_if_ready` достраивает снимок** перед решением о модуле: у
    ученика со снимком до среза 25 в нём только темы topic01, и закрытие
    последней из них иначе объявило бы «курс пройден» (ревью плана, M2).

## Review Focus

Случаи, которые спека подразумевает, но легко пропустить. Тест каждого живёт в
задаче, указанной в скобках:

1. **Ученик со старым снимком** (до среза 25: только темы topic01, без
   `topic_id`/`closed_at`) — продолжает с того же места, без «Маршрут
   перестроен», закрытое остаётся закрытым (задачи 6, 7).
2. **Нажатие в анкете, начатой до обновления** (в FSM нет ключа `topic`) —
   работает как анкета модуля 1, без падения (задача 12).
3. **Модуль, где всё отмечено «Уверенно»** — модуль начинается с проверки, а не
   с урока (задача 12).
4. **Возврат к теме пройденного модуля** — её закрытие без повторного «🎉»,
   дальше — незаконченный модуль (задача 8).
5. **Конец курса** — последняя тема последнего модуля: «🎓» одним сообщением,
   без пустого задания и без дубля «курс пройден»; `/plan` после этого не
   падает (задачи 8, 9).

---

## Подготовка

- [ ] Ветка: `git switch -c course-modules` (работа не идёт в `master`).
- [ ] Журнал: создать `.superpowers/sdd/2026-10-08-course-modules/progress.md` с
  разделом `## Setup` (база — хеш `git rev-parse HEAD`, 591 тест на старте).
- [ ] `.gitignore`: добавить строку `data/raw/` в раздел «Данные» — материалы
  курса не коммитятся (коммит `Срез 24: data/raw в .gitignore`).

## Срез 24 — данные и граф

### Task 1: Доменные модели модулей

**Files:**
- Modify: `src/llm_tutor/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Produces: `Concept.topic_id: int = 1`, `Chunk.topic_id: int = 1`,
  `SurveyBlock(key, title, question, example, concepts: tuple[str, ...])`,
  `SurveyConfig(level_key, level_question, level_options: tuple[str, ...],
  blocks: tuple[SurveyBlock, ...], assumed_by_confident, foundation)` с методом
  `block(key) -> SurveyBlock` и свойством `keys -> tuple[str, ...]`,
  `Topic(number, title, intro, survey: SurveyConfig, active=True)`. Все три
  новых модели заморожены (`frozen=True`) и хешируемы.

- [ ] **Step 1: Write the failing tests** — дописать в `tests/test_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from llm_tutor.schemas import Chunk, Concept, SurveyConfig, Topic


def _survey(**patch) -> dict:
    data = {
        "level_key": "t02_level",
        "level_question": "Как у тебя с графиками?",
        "level_options": ["С нуля", "Немного", "Уверенно"],
        "blocks": [
            {"key": "t02_a", "title": "А", "question": "А?", "example": "a", "concepts": ["x"]},
            {"key": "t02_b", "title": "Б", "question": "Б?", "example": "b", "concepts": ["y"]},
        ],
        "assumed_by_confident": ["t02_a"],
        "foundation": ["t02_a"],
    }
    data.update(patch)
    return data


def test_concept_and_chunk_default_to_first_module() -> None:
    assert Concept(id="a", name="a").topic_id == 1
    assert Chunk(source_url="u", content="c", seq=0).topic_id == 1


def test_survey_config_parses_lists_into_tuples() -> None:
    config = SurveyConfig.model_validate(_survey())

    assert config.block("t02_b").concepts == ("y",)
    assert config.keys == ("t02_level", "t02_a", "t02_b")


def test_survey_config_needs_three_level_options() -> None:
    with pytest.raises(ValidationError, match="три варианта"):
        SurveyConfig.model_validate(_survey(level_options=["С нуля", "Уверенно"]))


def test_survey_config_needs_blocks() -> None:
    with pytest.raises(ValidationError, match="нет блоков"):
        SurveyConfig.model_validate(_survey(blocks=[], assumed_by_confident=[], foundation=[]))


def test_survey_config_rejects_duplicate_keys() -> None:
    with pytest.raises(ValidationError, match="Дубли ключей"):
        SurveyConfig.model_validate(_survey(level_key="t02_a"))


def test_survey_config_rejects_unknown_block_references() -> None:
    with pytest.raises(ValidationError, match="неизвестные блоки"):
        SurveyConfig.model_validate(_survey(foundation=["t02_zzz"]))


def test_survey_config_unknown_block_key_raises() -> None:
    with pytest.raises(KeyError):
        SurveyConfig.model_validate(_survey()).block("nope")


def test_topic_is_frozen_and_hashable() -> None:
    topic = Topic(number=2, title="Т", intro="И", survey=SurveyConfig.model_validate(_survey()))

    assert hash(topic) == hash(topic.model_copy())
    with pytest.raises(ValidationError):
        topic.title = "другое"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schemas.py -q`
Expected: FAIL — `ImportError: cannot import name 'SurveyConfig'`.

- [ ] **Step 3: Implement** — в `src/llm_tutor/schemas.py`:

Импорт: `from pydantic import BaseModel, ConfigDict, Field, model_validator`.

В `Concept` перед `active`:

```python
    # Модуль курса (1–10). Проставляет загрузчик по seed-файлу модуля (срез 24);
    # у узлов из тестов и данных до модулей — первый модуль.
    topic_id: int = Field(default=1, ge=1)
```

В `Chunk` последним полем:

```python
    # Модуль, к которому относится материал: RAG не подмешивает будущие модули.
    topic_id: int = Field(default=1, ge=1)
```

После `Criterion` — новые модели:

```python
# Вопрос об общем уровне: «с нуля», «немного», «уверенно» (срез 22).
SURVEY_LEVEL_OPTIONS = 3


class SurveyBlock(BaseModel):
    """Блок анкеты модуля: ключ факта, имя для сводки, вопрос, пример API, темы."""

    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    question: str
    example: str
    concepts: tuple[str, ...]


class SurveyConfig(BaseModel):
    """Анкета модуля: вопрос об общем уровне и вопросы по блокам (срез 26).

    Живёт в seed модуля: у каждого модуля свои блоки, а правила ветвления
    общие (``student/survey.py``).
    """

    model_config = ConfigDict(frozen=True)

    level_key: str
    level_question: str
    level_options: tuple[str, ...]
    blocks: tuple[SurveyBlock, ...]
    # «Уверенно» в вопросе об уровне: эти блоки знакомы без вопроса.
    assumed_by_confident: tuple[str, ...] = ()
    # Фундамент: «Впервые вижу» здесь — дальше всё тоже незнакомо.
    foundation: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "SurveyConfig":
        """Анкета согласована сама с собой: иначе бот упал бы посреди вопросов."""
        if len(self.level_options) != SURVEY_LEVEL_OPTIONS:
            raise ValueError("в вопросе об уровне должно быть три варианта")
        if not self.blocks:
            raise ValueError("в анкете модуля нет блоков")
        keys = [self.level_key, *(block.key for block in self.blocks)]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Дубли ключей анкеты: {keys}")
        unknown = sorted(
            (set(self.assumed_by_confident) | set(self.foundation))
            - {block.key for block in self.blocks}
        )
        if unknown:
            raise ValueError(f"анкета ссылается на неизвестные блоки: {unknown}")
        return self

    @property
    def keys(self) -> tuple[str, ...]:
        """Ключи фактов анкеты: вопрос об уровне и все блоки."""
        return (self.level_key, *(block.key for block in self.blocks))

    def block(self, key: str) -> SurveyBlock:
        """Блок по ключу факта (``KeyError``, если такого нет)."""
        for block in self.blocks:
            if block.key == key:
                return block
        raise KeyError(f"Нет блока анкеты с ключом {key!r}")


class Topic(BaseModel):
    """Модуль курса: номер, название, вводная и анкета (срез 24)."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    title: str
    intro: str
    survey: SurveyConfig
    active: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное (591 + 8 новых).

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/schemas.py tests/test_schemas.py
git commit -m "Срез 24: модели модуля и анкеты модуля"
```

---

### Task 2: Миграция 004 и репозиторий

**Files:**
- Create: `migrations/004_topics.sql`
- Modify: `src/llm_tutor/db/connection.py` (`SCHEMA_VERSION = 4`)
- Modify: `src/llm_tutor/db/repos.py` (`_write_concept`, `get_concepts`,
  `replace_seed`, `replace_chunks`, новые `get_topics`, `get_topic`)
- Test: `tests/test_db.py`, `tests/test_repos.py`

**Interfaces:**
- Consumes: `Topic`, `SurveyConfig`, `Concept.topic_id`, `Chunk.topic_id` (задача 1).
- Produces: `repos.replace_seed(conn, concepts, edges, items, rubrics=(),
  criteria=(), topics: Sequence[Topic] = ())`; `repos.get_topics(conn) ->
  list[Topic]` (активные, по номеру); `repos.get_topic(conn, topic_id: int) ->
  Topic | None` (только активный); `get_concepts` отдаёт `topic_id`;
  `replace_chunks` пишет `chunk.topic_id`.

- [ ] **Step 1: Write the failing tests**

В `tests/test_db.py`:

```python
def test_migration_004_puts_existing_rows_into_first_module(monkeypatch, tmp_path) -> None:
    """Всё, что было до модулей, — первый модуль (topic01)."""
    for name in ("001_init.sql", "002_seed_active.sql", "003_rubrics_active.sql"):
        (tmp_path / name).write_text(
            (connection.MIGRATIONS_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(connection, "SCHEMA_VERSION", 3)
    old = get_conn(":memory:")
    try:
        migrate(old)
        old.execute("INSERT INTO concepts (id, name) VALUES ('groupby', 'groupby')")
        old.execute("INSERT INTO chunks (source_url, seq, content) VALUES ('u', 0, 'groupby')")
        old.commit()

        monkeypatch.undo()
        migrate(old)

        assert old.execute("SELECT topic_id FROM concepts").fetchone()[0] == 1
        assert old.execute("SELECT topic_id FROM chunks").fetchone()[0] == 1
        assert old.execute("SELECT count(*) FROM topics").fetchone()[0] == 0
    finally:
        old.close()
```

В `tests/test_repos.py`:

```python
from llm_tutor.schemas import Chunk, Concept, SurveyBlock, SurveyConfig, Topic


def _topic(number: int) -> Topic:
    return Topic(
        number=number,
        title=f"Модуль {number}",
        intro="Вводная",
        survey=SurveyConfig(
            level_key=f"t{number:02d}_level",
            level_question="Как ты?",
            level_options=("С нуля", "Немного", "Уверенно"),
            blocks=(
                SurveyBlock(
                    key=f"t{number:02d}_b", title="Блок", question="Знаешь?",
                    example="пример", concepts=("a",),
                ),
            ),
        ),
    )


def test_replace_seed_writes_topics_and_concept_module(conn) -> None:
    repos.replace_seed(conn, [Concept(id="a", name="a", topic_id=2)], [], [], topics=[_topic(2)])

    assert repos.get_topics(conn) == [_topic(2)]
    assert repos.get_topic(conn, 2) == _topic(2)
    assert repos.get_concepts(conn)[0].topic_id == 2


def test_replace_seed_deactivates_missing_topic(conn) -> None:
    repos.replace_seed(conn, [], [], [], topics=[_topic(1), _topic(2)])

    repos.replace_seed(conn, [], [], [], topics=[_topic(1)])

    assert [topic.number for topic in repos.get_topics(conn)] == [1]
    assert repos.get_topic(conn, 2) is None


def test_replace_chunks_keeps_module(conn) -> None:
    repos.replace_chunks(conn, "u", [Chunk(source_url="u", content="x", seq=0, topic_id=3)])

    assert conn.execute("SELECT topic_id FROM chunks").fetchone()[0] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py tests/test_repos.py -q`
Expected: FAIL — `no such column: topic_id` / `unexpected keyword argument 'topics'`.

- [ ] **Step 3: Implement**

`migrations/004_topics.sql`:

```sql
-- Миграция 004: модули курса (срез 24).
-- Курс — десять модулей mlcourse.ai. Узел и чанк знают свой модуль; всё, что
-- было до миграции, — первый модуль (topic01 «Pandas / EDA»).
-- Внешнего ключа на topics нет: ALTER TABLE ADD COLUMN с REFERENCES требует
-- NULL по умолчанию, а модуль у узла обязателен. Ссылки проверяет валидатор
-- курса (course/seed.py).

CREATE TABLE IF NOT EXISTS topics (
  id     INTEGER PRIMARY KEY,           -- номер модуля: 1..10
  title  TEXT NOT NULL,
  intro  TEXT NOT NULL DEFAULT '',
  survey TEXT NOT NULL,                 -- JSON конфига анкеты (schemas.SurveyConfig)
  active INTEGER NOT NULL DEFAULT 1
);

ALTER TABLE concepts ADD COLUMN topic_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE chunks ADD COLUMN topic_id INTEGER NOT NULL DEFAULT 1;
```

`connection.py`: `SCHEMA_VERSION = 4`.

`repos.py` — импорт `SurveyConfig, Topic` из `llm_tutor.schemas`. `_write_concept`:

```python
def _write_concept(conn: sqlite3.Connection, concept: Concept) -> None:
    """Upsert концепта без коммита (для вызова внутри чужой транзакции)."""
    conn.execute(
        "INSERT INTO concepts (id, name, difficulty, description, source_url, topic_id, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "name = excluded.name, difficulty = excluded.difficulty, "
        "description = excluded.description, source_url = excluded.source_url, "
        "topic_id = excluded.topic_id, "
        # Вернувшийся в seed узел снова активен.
        "active = 1",
        (
            concept.id,
            concept.name,
            concept.difficulty,
            concept.description,
            concept.source_url,
            concept.topic_id,
            int(concept.active),
        ),
    )
```

`get_concepts`: в `SELECT` добавить `topic_id`, в конструктор — `topic_id=r["topic_id"]`.

Новые функции рядом с рубриками:

```python
# --- модули курса (срез 24) ---


def _write_topic(conn: sqlite3.Connection, topic: Topic) -> None:
    """Upsert модуля без коммита; анкета хранится JSON-ом."""
    conn.execute(
        "INSERT INTO topics (id, title, intro, survey, active) VALUES (?, ?, ?, ?, 1) "
        "ON CONFLICT(id) DO UPDATE SET title = excluded.title, intro = excluded.intro, "
        "survey = excluded.survey, active = 1",
        (topic.number, topic.title, topic.intro, topic.survey.model_dump_json()),
    )


def _row_to_topic(row: sqlite3.Row) -> Topic:
    return Topic(
        number=row["id"],
        title=row["title"],
        intro=row["intro"],
        survey=SurveyConfig.model_validate_json(row["survey"]),
        active=bool(row["active"]),
    )


def get_topics(conn: sqlite3.Connection) -> list[Topic]:
    """Активные модули курса по возрастанию номера."""
    rows = conn.execute(
        "SELECT id, title, intro, survey, active FROM topics WHERE active = 1 ORDER BY id"
    ).fetchall()
    return [_row_to_topic(row) for row in rows]


def get_topic(conn: sqlite3.Connection, topic_id: int) -> Topic | None:
    """Активный модуль по номеру или ``None``."""
    row = conn.execute(
        "SELECT id, title, intro, survey, active FROM topics WHERE id = ? AND active = 1",
        (topic_id,),
    ).fetchone()
    return _row_to_topic(row) if row else None
```

`replace_seed`: новый параметр `topics: Sequence[Topic] = ()` последним; в
`try` первой строкой `for topic in topics: _write_topic(conn, topic)`, а к
вызовам `_deactivate_missing` добавить
`_deactivate_missing(conn, "topics", ("id",), {(t.number,) for t in topics})`.
В докстринг: «Модули курса — тоже часть seed (срез 24)».

`replace_chunks`: в `INSERT` добавить колонку `topic_id` и значение `chunk.topic_id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add migrations/004_topics.sql src/llm_tutor/db/connection.py src/llm_tutor/db/repos.py tests/test_db.py tests/test_repos.py
git commit -m "Срез 24: миграция 004 — модули курса в БД"
```

---

### Task 3: Граф курса по модулям

**Files:**
- Modify: `src/llm_tutor/course/graph.py`
- Create: `tests/course_fixtures.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `Concept.topic_id` (задача 1).
- Produces: `CourseGraph.topic_of(node_id) -> int`, `CourseGraph.topic_ids ->
  list[int]` (по возрастанию), `CourseGraph.topic_nodes(topic_id) -> list[str]`
  (в топопорядке); `topo_order()` — модуль за модулем; ребро вперёд между
  модулями — `CourseGraphError` с текстом «ведёт вперёд».
  `tests/course_fixtures.TOPIC01_ORDER: list[str]`.

- [ ] **Step 1: Write the failing tests**

`tests/course_fixtures.py`:

```python
"""Общие данные тестов курса из модулей (срезы 24–27)."""

# Топологический порядок topic01 до появления модулей (снят на коммите e54f3d0):
# переход на модули не должен его сдвинуть.
TOPIC01_ORDER = [
    "python_basics", "numpy_basics", "pandas_intro", "pandas_series",
    "pandas_dataframe", "groupby", "indexing_loc_iloc", "read_csv", "sorting",
    "value_counts", "agg_functions", "boolean_indexing", "df_inspect",
    "summary_tables", "apply_functions", "describe_stats", "dtype_conversion",
    "df_transformations", "visualization_basics", "eda_workflow", "churn_eda_case",
]
```

В `tests/test_graph.py` (импорт `from course_fixtures import TOPIC01_ORDER`):

```python
def _modules(nodes: dict[str, int], edges: list[tuple[str, str]]) -> CourseGraph:
    return CourseGraph(
        [Concept(id=node, name=node, topic_id=topic) for node, topic in nodes.items()],
        [Edge(from_id=src, to_id=dst, hard=True) for src, dst in edges],
    )


def test_topo_order_goes_module_by_module() -> None:
    graph = _modules({"b2": 2, "a1": 1, "c1": 1}, [("a1", "b2")])

    assert graph.topo_order()[-1] == "b2"


def test_forward_edge_between_modules_is_rejected() -> None:
    with pytest.raises(CourseGraphError, match="вперёд"):
        _modules({"a1": 1, "b2": 2}, [("b2", "a1")])


def test_topic_helpers() -> None:
    graph = _modules({"a1": 1, "b2": 2, "c2": 2}, [("b2", "c2")])

    assert graph.topic_of("c2") == 2
    assert graph.topic_ids == [1, 2]
    assert graph.topic_nodes(2) == ["b2", "c2"]


def test_topic01_order_is_unchanged(conn) -> None:
    load_seed(conn)

    assert CourseGraph.load(conn).topo_order() == TOPIC01_ORDER
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph.py -q`
Expected: FAIL — `AttributeError: 'CourseGraph' object has no attribute 'topic_of'`,
ребро вперёд не падает.

- [ ] **Step 3: Implement** — в `graph.py`:

Конец `__init__` (вместо одиночного `self.validate_dag()`):

```python
        self._check_topic_direction()
        self.validate_dag()
        # Граф неизменяем: порядок считаем один раз (маршрут зовёт его каждый ход).
        self._topo = self._module_topo_order()
```

Новые методы:

```python
    def _check_topic_direction(self) -> None:
        """Пререквизит не может лежать в модуле позже зависимой темы (срез 24).

        На этом держится топопорядок модуль за модулем: ребро вперёд сделало бы
        склейку порядков модулей неверной.
        """
        for src, dst in self._graph.edges:
            src_topic = self._concepts[src].topic_id
            dst_topic = self._concepts[dst].topic_id
            if src_topic > dst_topic:
                raise CourseGraphError(
                    f"Ребро {src} -> {dst} ведёт вперёд: из модуля {src_topic} "
                    f"в модуль {dst_topic}"
                )

    def _module_topo_order(self) -> list[str]:
        """Модуль за модулем, внутри модуля — прежний ``nx.topological_sort``.

        Подграф строится заново, а не через ``subgraph``: вид подграфа при малой
        доле узлов перебирает их в порядке множества (хеш строк, меняется от
        запуска к запуску). Узлы и рёбра добавляются в порядке исходного графа,
        поэтому порядок topic01 тот же, что до появления модулей.
        """
        order: list[str] = []
        for topic_id in self.topic_ids:
            members = [n for n in self._graph if self._concepts[n].topic_id == topic_id]
            inside = set(members)
            module = nx.DiGraph()
            module.add_nodes_from(members)
            module.add_edges_from(
                (src, dst) for src, dst in self._graph.edges if src in inside and dst in inside
            )
            order.extend(nx.topological_sort(module))
        return order

    @property
    def topic_ids(self) -> list[int]:
        """Номера модулей, у которых есть темы, по возрастанию."""
        return sorted({concept.topic_id for concept in self._concepts.values()})

    def topic_of(self, node_id: str) -> int:
        """Модуль курса, к которому относится тема."""
        return self.concept(node_id).topic_id

    def topic_nodes(self, topic_id: int) -> list[str]:
        """Темы модуля в топологическом порядке."""
        return [node for node in self._topo if self._concepts[node].topic_id == topic_id]
```

`topo_order`:

```python
    def topo_order(self) -> list[str]:
        """Топологический порядок: модуль за модулем, внутри — по пререквизитам."""
        return list(self._topo)
```

Докстринг модуля дополнить абзацем: «Узел знает свой модуль (срез 24):
межмодульные рёбра ведут только назад, порядок обхода — модуль за модулем».

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/course/graph.py tests/course_fixtures.py tests/test_graph.py
git commit -m "Срез 24: граф курса знает модули, порядок — модуль за модулем"
```

---

### Task 4: Курс из модулей — формат seed и загрузка

**Files:**
- Modify: `data/seed_topic01.json` (раздел `topic`)
- Create: `tests/fixtures/seed_topic02_mini.json`
- Modify: `tests/course_fixtures.py`
- Modify (переписать): `src/llm_tutor/course/seed.py`
- Modify: `src/llm_tutor/bot/main.py`
- Modify: `docs/superpowers/specs/2026-10-08-course-modules-design.md` (отступления 1–10)
- Test: `tests/test_course.py` (новый), `tests/test_course_data.py` (новый), `tests/test_graph.py`

**Interfaces:**
- Consumes: `Topic`, `repos.replace_seed(..., topics=)`, `CourseGraph` (задачи 1–3).
- Produces (`llm_tutor.course.seed`): `Seed` (поле `topic: Topic` обязательно),
  `Course` (frozen dataclass: `topics, nodes, edges, items, rubrics, criteria:
  tuple[...]`, `warnings: tuple[str, ...]`), `SeedError`, `DATA_DIR`,
  `DEFAULT_SEED_PATH`, `course_paths(data_dir: Path | None = None) -> list[Path]`,
  `build_course(seeds: Sequence[Seed]) -> Course`,
  `load_course_data(paths: Sequence[str | Path] | None = None) -> Course`,
  `load_course(conn, paths=None) -> Course`, `load_seed_data(path) -> Seed`,
  `load_seed(conn, path) -> Seed` (один модуль как весь курс),
  `nodes_without_items(seed: Seed | Course)`, `items_without_rubric(seed: Seed | Course)`.
  `tests/course_fixtures`: `MINI_SEED_PATH`, `T1: SurveyConfig`, `T2: SurveyConfig`,
  `load_two_modules(conn) -> Course`, `correct_answer(item) -> str`.

- [ ] **Step 1: Добавить раздел `topic` в `data/seed_topic01.json`** — сразу после
  строки `"comment": …,` вставить (тексты анкеты — дословно из `student/survey.py`):

```json
  "topic": {
    "number": 1,
    "title": "Первичный анализ данных с Pandas",
    "intro": "Знакомимся с pandas: загружаем таблицу, осматриваем её, фильтруем, группируем и ищем закономерности в данных об оттоке клиентов.",
    "survey": {
      "level_key": "survey_level",
      "level_question": "Для начала — как у тебя с Python и pandas?",
      "level_options": ["С нуля", "Немного знаю", "Уверенно работаю с pandas"],
      "blocks": [
        {"key": "block_python", "title": "Python и NumPy", "question": "Пишешь на Python и NumPy?", "example": "списки, функции, np.array", "concepts": ["python_basics", "numpy_basics"]},
        {"key": "block_tables", "title": "Таблицы pandas", "question": "Создаёшь Series и DataFrame?", "example": "индекс, столбцы, pd.DataFrame", "concepts": ["pandas_intro", "pandas_series", "pandas_dataframe"]},
        {"key": "block_loading", "title": "Чтение и осмотр", "question": "Загружаешь и осматриваешь данные?", "example": "read_csv, info, describe, astype", "concepts": ["read_csv", "df_inspect", "describe_stats", "dtype_conversion"]},
        {"key": "block_selection", "title": "Выборка и сортировка", "question": "Фильтруешь и сортируешь таблицы?", "example": "loc / iloc, условия, sort_values, value_counts", "concepts": ["indexing_loc_iloc", "boolean_indexing", "sorting", "value_counts", "df_transformations"]},
        {"key": "block_analysis", "title": "Группировки и EDA", "question": "Группируешь и исследуешь данные?", "example": "apply, groupby + agg, pivot_table, графики", "concepts": ["apply_functions", "groupby", "agg_functions", "summary_tables", "visualization_basics", "eda_workflow", "churn_eda_case"]}
      ],
      "assumed_by_confident": ["block_python", "block_tables", "block_loading"],
      "foundation": ["block_python", "block_tables"]
    }
  },
```

- [ ] **Step 2: Создать `tests/fixtures/seed_topic02_mini.json`** (тестовый мини-модуль
  с межмодульными рёбрами и межмодульным весом задания):

```json
{
  "course": "test/topic02_mini",
  "comment": "Тестовый мини-модуль 2: четыре темы, межмодульные рёбра к topic01, анкета.",
  "topic": {
    "number": 2,
    "title": "Визуальный анализ (тестовый мини-модуль)",
    "intro": "Тестовый модуль: графики pandas и связи признаков.",
    "survey": {
      "level_key": "t02_level",
      "level_question": "Как у тебя с графиками?",
      "level_options": ["С нуля", "Немного рисовал", "Уверенно строю графики"],
      "blocks": [
        {"key": "t02_basics", "title": "Простые графики", "question": "Строишь простые графики?", "example": "df.plot, hist", "concepts": ["mini_plots", "mini_hist"]},
        {"key": "t02_relations", "title": "Распределения и связи", "question": "Сравниваешь распределения и связи?", "example": "boxplot, corr", "concepts": ["mini_box", "mini_corr"]}
      ],
      "assumed_by_confident": ["t02_basics"],
      "foundation": ["t02_basics"]
    }
  },
  "nodes": [
    {"id": "mini_plots", "name": "Графики в pandas", "difficulty": 0.3, "description": "df.plot и его виды.", "source_url": null},
    {"id": "mini_hist", "name": "Гистограмма", "difficulty": 0.3, "description": "Распределение одного количественного признака.", "source_url": null},
    {"id": "mini_box", "name": "Ящик с усами", "difficulty": 0.4, "description": "Медиана, квартили и выбросы на одном графике.", "source_url": null},
    {"id": "mini_corr", "name": "Матрица корреляций", "difficulty": 0.45, "description": "Попарные корреляции количественных признаков.", "source_url": null}
  ],
  "edges": [
    {"from_id": "pandas_dataframe", "to_id": "mini_plots", "type": "requires", "hard": false, "weight": 0.5},
    {"from_id": "mini_plots", "to_id": "mini_hist", "type": "requires", "hard": true, "weight": 1.0},
    {"from_id": "mini_plots", "to_id": "mini_box", "type": "requires", "hard": true, "weight": 1.0},
    {"from_id": "describe_stats", "to_id": "mini_box", "type": "requires", "hard": false, "weight": 0.5},
    {"from_id": "mini_hist", "to_id": "mini_corr", "type": "requires", "hard": true, "weight": 1.0}
  ],
  "items": [
    {"id": 2001, "prompt": "Какой метод DataFrame строит график средствами pandas?", "answer_type": "choice", "concept_weights": {"mini_plots": 1.0}, "difficulty": 0.3, "options": ["df.plot()", "df.draw()", "df.chart()"], "answer": "0"},
    {"id": 2002, "prompt": "Что задаёт параметр kind в df.plot?", "answer_type": "choice", "concept_weights": {"mini_plots": 1.0}, "difficulty": 0.3, "options": ["Вид графика: line, bar, hist и другие", "Цвет линий", "Размер шрифта подписей"], "answer": "0"},
    {"id": 2003, "prompt": "Что показывает гистограмма?", "answer_type": "choice", "concept_weights": {"mini_hist": 1.0}, "difficulty": 0.3, "options": ["Распределение значений одного признака", "Связь двух признаков", "Долю пропусков в столбцах"], "answer": "0"},
    {"id": 2004, "prompt": "Что задаёт параметр bins у гистограммы?", "answer_type": "choice", "concept_weights": {"mini_hist": 1.0}, "difficulty": 0.3, "options": ["Число интервалов разбиения", "Число строк выборки", "Ширину рисунка"], "answer": "0"},
    {"id": 2005, "prompt": "Что отмечает линия внутри ящика на boxplot?", "answer_type": "choice", "concept_weights": {"mini_box": 1.0}, "difficulty": 0.4, "options": ["Медиану", "Среднее", "Максимум"], "answer": "0"},
    {"id": 2006, "prompt": "Между какими квантилями лежит ящик на boxplot?", "answer_type": "choice", "concept_weights": {"mini_box": 1.0, "describe_stats": 0.5}, "difficulty": 0.4, "options": ["Между первым и третьим квартилями", "Между минимумом и максимумом", "Между средним и медианой"], "answer": "0"},
    {"id": 2007, "prompt": "Какой метод DataFrame считает матрицу корреляций?", "answer_type": "choice", "concept_weights": {"mini_corr": 1.0}, "difficulty": 0.45, "options": ["df.corr()", "df.cov_matrix()", "df.relate()"], "answer": "0"},
    {"id": 2008, "prompt": "Какое значение коэффициента Пирсона означает отсутствие линейной связи?", "answer_type": "choice", "concept_weights": {"mini_corr": 1.0}, "difficulty": 0.45, "options": ["0", "1", "-1"], "answer": "0"}
  ],
  "rubrics": [],
  "criteria": []
}
```

- [ ] **Step 3: Дописать `tests/course_fixtures.py`**:

```python
from pathlib import Path

from llm_tutor.course.seed import (
    DEFAULT_SEED_PATH,
    Course,
    load_course,
    load_course_data,
    load_seed_data,
)
from llm_tutor.schemas import Item

MINI_SEED_PATH = Path(__file__).parent / "fixtures" / "seed_topic02_mini.json"

# Анкеты модулей: topic01 и тестового мини-модуля 2.
T1 = load_seed_data(DEFAULT_SEED_PATH).topic.survey
T2 = load_course_data([DEFAULT_SEED_PATH, MINI_SEED_PATH]).topics[1].survey


def load_two_modules(conn) -> Course:
    """Курс «topic01 + мини-модуль 2» в БД — как бот на старте."""
    return load_course(conn, [DEFAULT_SEED_PATH, MINI_SEED_PATH])


def correct_answer(item: Item) -> str:
    """Текст верного ответа на задание с автопроверкой."""
    return item.options[int(item.answer)] if item.answer_type == "choice" else item.answer
```

- [ ] **Step 4: Write the failing tests**

`tests/test_course.py`:

```python
"""Курс из модулей: загрузка всех seed-файлов одним проходом (срез 24)."""

import json
from pathlib import Path

import pytest

from course_fixtures import MINI_SEED_PATH, TOPIC01_ORDER, load_two_modules
from llm_tutor.course.graph import CourseGraph, CourseGraphError
from llm_tutor.course.seed import (
    DEFAULT_SEED_PATH,
    SeedError,
    course_paths,
    load_course_data,
    load_seed,
    load_seed_data,
    main,
)
from llm_tutor.db import repos
from llm_tutor.student import survey


def _mini() -> dict:
    return json.loads(MINI_SEED_PATH.read_text(encoding="utf-8"))


def _course_with(tmp_path, data: dict, name: str = "seed_topic02_mini.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_course_data([DEFAULT_SEED_PATH, path])


def test_two_modules_load_without_deactivating_first(conn) -> None:
    course = load_two_modules(conn)
    graph = CourseGraph.load(conn)

    assert [topic.number for topic in repos.get_topics(conn)] == [1, 2]
    assert graph.topic_of("groupby") == 1 and graph.topic_of("mini_plots") == 2
    assert len(graph.node_ids) == len(course.nodes)
    assert graph.topo_order()[: len(TOPIC01_ORDER)] == TOPIC01_ORDER


def test_single_module_load_replaces_whole_course(conn) -> None:
    """load_seed — один модуль как весь курс: остальное гаснет."""
    load_two_modules(conn)

    load_seed(conn)

    assert [topic.number for topic in repos.get_topics(conn)] == [1]
    assert not CourseGraph.load(conn).has_node("mini_plots")


def test_module_file_alone_is_not_a_course() -> None:
    """Модуль 2 ссылается на темы модуля 1 — отдельно он не валиден."""
    with pytest.raises((SeedError, CourseGraphError)):
        load_course_data([MINI_SEED_PATH])


def test_forward_edge_between_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["edges"].append(
        {"from_id": "mini_corr", "to_id": "groupby", "type": "requires", "hard": False, "weight": 0.5}
    )
    with pytest.raises(CourseGraphError, match="вперёд"):
        _course_with(tmp_path, data)


def test_duplicate_item_id_across_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["items"][0]["id"] = 1
    with pytest.raises(SeedError, match="Дубли id заданий"):
        _course_with(tmp_path, data)


def test_duplicate_node_across_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["nodes"].append({"id": "groupby", "name": "дубль"})
    with pytest.raises(SeedError, match="Дубли id узлов"):
        _course_with(tmp_path, data)


def test_duplicate_survey_key_across_modules_is_rejected(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["level_key"] = "survey_level"
    with pytest.raises(SeedError, match="ключей анкеты"):
        _course_with(tmp_path, data)


def test_survey_must_cover_every_module_node(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["blocks"][1]["concepts"] = ["mini_box"]
    with pytest.raises(SeedError, match="не накрывает"):
        _course_with(tmp_path, data)


def test_survey_cannot_claim_foreign_nodes(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["blocks"][1]["concepts"].append("groupby")
    with pytest.raises(SeedError, match="чужие темы"):
        _course_with(tmp_path, data)


def test_node_in_two_blocks_is_rejected(tmp_path) -> None:
    data = _mini()
    data["topic"]["survey"]["blocks"][1]["concepts"].append("mini_plots")
    with pytest.raises(SeedError, match="нескольких блоках"):
        _course_with(tmp_path, data)


def test_file_name_must_match_module_number(tmp_path) -> None:
    with pytest.raises(SeedError, match="в имени"):
        _course_with(tmp_path, _mini(), name="seed_topic03.json")


def test_same_module_number_twice_is_rejected(tmp_path) -> None:
    data = _mini()
    data["topic"]["number"] = 1
    with pytest.raises(SeedError, match="модулей"):
        _course_with(tmp_path, data, name="seed_topic01_twin.json")


def test_hard_cross_module_edge_is_a_warning(tmp_path) -> None:
    data = _mini()
    data["edges"][0]["hard"] = True

    course = _course_with(tmp_path, data)

    assert any("pandas_dataframe -> mini_plots" in warning for warning in course.warnings)


def test_course_paths_take_only_module_files(tmp_path) -> None:
    for name in ("seed_topic02.json", "seed_topic01.json", "golden_set_topic01.json", "seed.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert [path.name for path in course_paths(tmp_path)] == ["seed_topic01.json", "seed_topic02.json"]


def test_empty_data_dir_reports_clearly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("llm_tutor.course.seed.DATA_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="Нет seed-файлов"):
        load_course_data()


def test_seed_cli_loads_whole_course(tmp_path, capsys) -> None:
    assert main(["--db", str(tmp_path / "t.db")]) == 0
    assert "модул" in capsys.readouterr().out


def test_topic01_seed_carries_its_survey_verbatim() -> None:
    """Анкета переехала в seed без изменений (удаляется в задаче 11 вместе с константами)."""
    config = load_seed_data(DEFAULT_SEED_PATH).topic.survey

    assert config.level_question == survey.LEVEL_QUESTION
    assert config.level_options == survey.LEVEL_OPTIONS
    assert [(b.key, b.title, b.question, b.example, b.concepts) for b in config.blocks] == [
        (b.key, b.title, b.question, b.example, b.concepts) for b in survey.BLOCKS
    ]
    assert config.assumed_by_confident == survey.ASSUMED_BY_CONFIDENT
    assert config.foundation == survey.FOUNDATION


def test_topic01_survey_keys_never_change() -> None:
    """На этих ключах лежат факты в рабочей БД — менять их нельзя."""
    assert load_seed_data(DEFAULT_SEED_PATH).topic.survey.keys == (
        "survey_level", "block_python", "block_tables",
        "block_loading", "block_selection", "block_analysis",
    )
```

`tests/test_course_data.py` (реальные файлы курса — защищает срезы контента):

```python
"""Реальные seed-файлы курса: все модули грузятся и годятся для урока (срез 24)."""

from llm_tutor.course.graph import CourseGraph
from llm_tutor.course.seed import load_course
from llm_tutor.db import repos
from llm_tutor.student import diagnostic


def test_modules_are_numbered_from_one_without_gaps(conn) -> None:
    numbers = [topic.number for topic in load_course(conn).topics]

    assert numbers == list(range(1, len(numbers) + 1))


def test_every_module_has_15_to_25_topics(conn) -> None:
    load_course(conn)
    graph = CourseGraph.load(conn)

    for topic_id in graph.topic_ids:
        assert 15 <= len(graph.topic_nodes(topic_id)) <= 25, topic_id


def test_every_node_has_two_auto_checked_items(conn) -> None:
    load_course(conn)
    graph = CourseGraph.load(conn)
    items = repos.get_items(conn)

    for node_id in graph.node_ids:
        auto = [
            item for item in items
            if item.answer_type in diagnostic.AUTO_CHECKABLE and node_id in item.concept_weights
        ]
        assert len(auto) >= 2, f"у темы {node_id} меньше двух заданий с автопроверкой"


def test_choice_items_are_unambiguous(conn) -> None:
    load_course(conn)

    for item in repos.get_items(conn):
        if item.answer_type == "choice":
            assert 3 <= len(item.options) <= 5, item.id
            assert len(set(item.options)) == len(item.options), item.id


def test_items_lean_only_on_earlier_modules(conn) -> None:
    """Вторичный вес задания — на тему своего или прошлого модуля, не будущего."""
    load_course(conn)
    graph = CourseGraph.load(conn)

    for item in repos.get_items(conn):
        main = max(item.concept_weights, key=item.concept_weights.get)
        assert all(
            graph.topic_of(node) <= graph.topic_of(main) for node in item.concept_weights
        ), item.id
```

В `tests/test_graph.py` — тест `test_seed_cli_loads_into_db` проверяет «концептов»;
текст вывода сохраняет это слово, правка не нужна. Хелпер `_write_trimmed_seed`
убирает узлы из `nodes`, но не из анкеты — новое правило «анкета ссылается на
чужие темы» уронило бы `test_load_seed_prunes_stale_concepts` и
`test_load_seed_deactivates_concept_with_events`. В хелпер перед записью файла
добавить:

```python
    # Анкета модуля накрывает ровно его темы (срез 24): убранная тема уходит и из блока.
    for block in data["topic"]["survey"]["blocks"]:
        block["concepts"] = [c for c in block["concepts"] if c not in drop_nodes]
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `uv run pytest tests/test_course.py tests/test_course_data.py -q`
Expected: FAIL — `ImportError: cannot import name 'course_paths'` / `Seed` без поля `topic`.

- [ ] **Step 6: Implement** — переписать `src/llm_tutor/course/seed.py` целиком:

```python
"""Загрузка курса в БД: seed-файлы модулей (срез 4.1, модули — срез 24).

Курс — десять модулей mlcourse.ai, по seed-файлу на модуль
(``data/seed_topicNN.json``). В БД все файлы ложатся ОДНИМ проходом
``replace_seed``: по очереди нельзя — каждый следующий погасил бы предыдущий.
Перед записью курс валидируется целиком: ссылки между модулями, рёбра только
назад (``CourseGraph``), анкета модуля накрывает его темы, циклов нет.

CLI: ``python -m llm_tutor.course.seed [--seed PATH] [--db PATH]``.
"""

import json
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Criterion, Edge, Item, Rubric, Topic

# Корень проекта (src/llm_tutor/course/seed.py → src → корень).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _PROJECT_ROOT / "data"
DEFAULT_SEED_PATH = DATA_DIR / "seed_topic01.json"
# Имя seed-файла модуля: seed_topicNN.json (суффикс — для тестовых модулей).
_SEED_NAME_RE = re.compile(r"seed_topic(\d{2})(?:_\w+)?\.json")


class Seed(BaseModel):
    """Seed-файл модуля: модуль, его граф, банк заданий и рубрики."""

    course: str
    topic: Topic
    nodes: list[Concept]
    edges: list[Edge]
    items: list[Item] = Field(default_factory=list)
    rubrics: list[Rubric] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)


@dataclass(frozen=True)
class Course:
    """Курс целиком: модули и склеенные разделы их seed-файлов."""

    topics: tuple[Topic, ...]
    nodes: tuple[Concept, ...]
    edges: tuple[Edge, ...]
    items: tuple[Item, ...]
    rubrics: tuple[Rubric, ...]
    criteria: tuple[Criterion, ...]
    # Не ошибки, но автору стоит посмотреть: жёсткие межмодульные рёбра.
    warnings: tuple[str, ...] = ()


class SeedError(ValueError):
    """Курс внутренне несогласован: битые ссылки между разделами или модулями."""


def _check_unique(title: str, ids: Sequence) -> None:
    """Дубли id молча схлопнулись бы в upsert — содержимое разошлось бы с файлом."""
    if len(set(ids)) != len(ids):
        dups = sorted({value for value in ids if ids.count(value) > 1}, key=str)
        raise SeedError(f"Дубли id {title}: {dups}")


def _check_references(
    nodes: Sequence[Concept],
    items: Sequence[Item],
    rubrics: Sequence[Rubric],
    criteria: Sequence[Criterion],
) -> None:
    """Проверяет ссылки между разделами курса.

    Опечатка в id рубрики или концепта иначе всплыла бы сырым ``IntegrityError``
    на старте бота (или, хуже, падением при ответе ученика) — а не подсказкой
    автору, который правит банк руками.
    """
    for title, ids in (
        ("узлов", [node.id for node in nodes]),
        ("рубрик", [rubric.id for rubric in rubrics]),
        ("критериев", [criterion.id for criterion in criteria]),
        ("заданий", [item.id for item in items]),
    ):
        _check_unique(title, ids)

    rubric_ids = {rubric.id for rubric in rubrics}
    node_ids = {node.id for node in nodes}
    for criterion in criteria:
        if criterion.rubric_id not in rubric_ids:
            raise SeedError(
                f"Критерий {criterion.id} ссылается на неизвестную рубрику "
                f"{criterion.rubric_id}"
            )
    for item in items:
        if item.rubric_id is not None and item.rubric_id not in rubric_ids:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестную рубрику {item.rubric_id}"
            )
        unknown = sorted(set(item.concept_weights) - node_ids)
        if unknown:
            raise SeedError(
                f"Задание {item.id} ссылается на неизвестные концепты: {unknown}"
            )


def _check_topics(seeds: Sequence[Seed]) -> list[str]:
    """Правила модулей; отдаёт предупреждения (жёсткие межмодульные рёбра).

    Направление рёбер (только назад) проверяет ``CourseGraph`` — здесь его
    повторять незачем.
    """
    _check_unique("модулей", [seed.topic.number for seed in seeds])
    keys = [key for seed in seeds for key in seed.topic.survey.keys]
    if len(set(keys)) != len(keys):
        dups = sorted({key for key in keys if keys.count(key) > 1})
        raise SeedError(f"Дубли ключей анкеты между модулями: {dups}")

    topic_of = {node.id: node.topic_id for seed in seeds for node in seed.nodes}
    warnings: list[str] = []
    for seed in seeds:
        number = seed.topic.number
        for edge in seed.edges:
            src, dst = topic_of[edge.from_id], topic_of[edge.to_id]
            if edge.type == "requires" and edge.hard and src < dst:
                warnings.append(
                    f"Жёсткое межмодульное ребро {edge.from_id} -> {edge.to_id} "
                    f"(модуль {src} -> {dst})"
                )
        own = {node.id for node in seed.nodes}
        covered = [c for block in seed.topic.survey.blocks for c in block.concepts]
        foreign = sorted(set(covered) - own)
        if foreign:
            raise SeedError(f"Анкета модуля {number} ссылается на чужие темы: {foreign}")
        missing = sorted(own - set(covered))
        if missing:
            raise SeedError(f"Анкета модуля {number} не накрывает темы: {missing}")
        twice = sorted({c for c in covered if covered.count(c) > 1})
        if twice:
            raise SeedError(f"Темы в нескольких блоках анкеты модуля {number}: {twice}")
    return warnings


def _parse_seed(path: str | Path) -> Seed:
    """Читает seed-файл модуля; темам проставляет модуль по файлу."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Seed-файл не найден: {source}")
    seed = Seed.model_validate(json.loads(source.read_text(encoding="utf-8")))
    number = seed.topic.number
    match = _SEED_NAME_RE.fullmatch(source.name)
    if match and int(match.group(1)) != number:
        raise SeedError(
            f"{source.name}: в файле модуль {number}, а в имени — {int(match.group(1))}"
        )
    # Модуль темы задаёт файл, а не поле темы: тема не «уедет» в чужой модуль.
    return seed.model_copy(
        update={"nodes": [node.model_copy(update={"topic_id": number}) for node in seed.nodes]}
    )


def build_course(seeds: Sequence[Seed]) -> Course:
    """Склеивает модули и валидирует курс целиком (``SeedError`` на ошибке)."""
    ordered = sorted(seeds, key=lambda seed: seed.topic.number)
    nodes = [node for seed in ordered for node in seed.nodes]
    edges = [edge for seed in ordered for edge in seed.edges]
    items = [item for seed in ordered for item in seed.items]
    rubrics = [rubric for seed in ordered for rubric in seed.rubrics]
    criteria = [criterion for seed in ordered for criterion in seed.criteria]
    _check_references(nodes, items, rubrics, criteria)
    CourseGraph(nodes, edges)  # цикл, битое ребро, ребро вперёд между модулями
    warnings = _check_topics(ordered)
    return Course(
        topics=tuple(seed.topic for seed in ordered),
        nodes=tuple(nodes),
        edges=tuple(edges),
        items=tuple(items),
        rubrics=tuple(rubrics),
        criteria=tuple(criteria),
        warnings=tuple(warnings),
    )


def course_paths(data_dir: Path | None = None) -> list[Path]:
    """Seed-файлы модулей курса (черновики и прочие файлы не берутся)."""
    folder = DATA_DIR if data_dir is None else data_dir
    return sorted(
        path for path in folder.glob("seed_topic*.json") if _SEED_NAME_RE.fullmatch(path.name)
    )


def load_course_data(paths: Sequence[str | Path] | None = None) -> Course:
    """Читает и валидирует модули курса (без записи в БД)."""
    chosen = course_paths() if paths is None else [Path(path) for path in paths]
    if not chosen:
        raise FileNotFoundError(f"Нет seed-файлов модулей в {DATA_DIR}")
    return build_course([_parse_seed(path) for path in chosen])


def load_seed_data(path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Читает и валидирует один seed-файл как курс из одного модуля."""
    seed = _parse_seed(path)
    build_course([seed])
    return seed


def _apply(conn: sqlite3.Connection, course: Course) -> None:
    repos.replace_seed(
        conn,
        course.nodes,
        course.edges,
        course.items,
        course.rubrics,
        course.criteria,
        topics=course.topics,
    )


def load_course(
    conn: sqlite3.Connection, paths: Sequence[str | Path] | None = None
) -> Course:
    """Идемпотентно приводит курс в БД к seed-файлам модулей (истина — seed)."""
    course = load_course_data(paths)
    _apply(conn, course)
    return course


def load_seed(conn: sqlite3.Connection, path: str | Path = DEFAULT_SEED_PATH) -> Seed:
    """Один модуль как весь курс — для тестов и отладки одного файла.

    Всё, чего нет в файле, гасится: в том числе другие модули.
    """
    seed = _parse_seed(path)
    _apply(conn, build_course([seed]))
    return seed


def items_without_rubric(seed: Seed | Course) -> list[int]:
    """Открытые и код-задания без рубрики: проверить их нечем, но они грузятся.

    Задание не выдаётся (рубрики нет), но автору об этом надо сказать вслух —
    иначе вопрос просто пропадёт из практики.
    """
    return sorted(
        item.id
        for item in seed.items
        if item.answer_type in ("open", "code") and item.rubric_id is None
    )


def nodes_without_items(seed: Seed | Course) -> list[str]:
    """«Несущие» узлы без задания в банке — диагностика их проверить не сможет.

    Возвращаются только узлы с зависимыми: непроверенная база запирает весь
    маршрут, тогда как лист без задания — терпимо.
    """
    graph = CourseGraph(seed.nodes, seed.edges)
    covered = {concept_id for item in seed.items for concept_id in item.concept_weights}
    return sorted(
        node_id
        for node_id in graph.node_ids
        if node_id not in covered and graph.dependents(node_id)
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: загрузить курс (или один seed-файл модуля) в БД."""
    import argparse

    from llm_tutor.db.connection import get_conn, migrate

    parser = argparse.ArgumentParser(description="Загрузка курса в БД.")
    parser.add_argument(
        "--seed",
        default=None,
        help=(
            "один seed-файл модуля — только для отладки на отдельной БД: "
            "остальные модули в ней гаснут; по умолчанию — все модули из data/"
        ),
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "data/llm_tutor.sqlite3"),
        help="путь к БД (по умолчанию DB_PATH или data/llm_tutor.sqlite3)",
    )
    args = parser.parse_args(argv)

    conn = get_conn(args.db)
    try:
        migrate(conn)
        course = load_course(conn, None if args.seed is None else [args.seed])
    finally:
        conn.close()

    print(
        f"Загружено: {len(course.topics)} модулей, {len(course.nodes)} концептов, "
        f"{len(course.edges)} рёбер, {len(course.items)} заданий"
    )
    for warning in course.warnings:
        print(f"Предупреждение: {warning}")
    missing = nodes_without_items(course)
    if missing:
        print(f"Без заданий (диагностика их не возьмёт): {', '.join(missing)}")
    without_rubric = items_without_rubric(course)
    if without_rubric:
        print(f"Открытые задания без рубрики (проверить нечем): {without_rubric}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

В `bot/main.py`: импорт `items_without_rubric, load_course, nodes_without_items`;
вместо `seed = load_seed(conn)  # …`:

```python
        course = load_course(conn)  # все модули курса — один проход replace_seed
        for warning in course.warnings:
            logging.warning(warning)
        missing = nodes_without_items(course)
```

и `items_without_rubric(course)` ниже.

В спеке `docs/superpowers/specs/2026-10-08-course-modules-design.md` добавить в
конец раздел `## 12. Отступления, найденные при планировании` со списком
«Отступления от спеки (Ruling)» 1–10 из этого плана (тексты дословно).

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное. Если старый тест полагался на прежний текст `SeedError`
(«Дубли id …») — проверить, что он по-прежнему совпадает (`match="Дубли id"`).

- [ ] **Step 8: Commit**

```bash
git add data/seed_topic01.json tests/fixtures/seed_topic02_mini.json tests/course_fixtures.py tests/test_course.py tests/test_course_data.py tests/test_graph.py src/llm_tutor/course/seed.py src/llm_tutor/bot/main.py docs/superpowers/specs/2026-10-08-course-modules-design.md
git commit -m "Срез 24: курс из модулей — раздел topic в seed и загрузка одним проходом"
```

---

### Task 5: Аудит среза 24

- [ ] **Step 1:** Записать в журнал (`progress.md`) раздел `## Срез 24` со
  списком коммитов (`git log --oneline <база>..HEAD`).
- [ ] **Step 2:** Запустить адверсариальное ревью агентом `general-purpose`
  (агента `fork` может не быть) с ТЗ из **Приложения Б**, подставив: срез 24,
  задачи 1–4, диапазон коммитов, фокус — миграция на копии рабочей БД
  (`data/llm_tutor.sqlite3` копируется во временный каталог, мигрирует,
  `load_course` проходит, `CourseGraph.load` строится, факты анкеты на месте),
  дубли и ссылки между модулями, порядок topic01, CLI `seed` на пустом и битом
  `data/`.
- [ ] **Step 3:** Триаж находок в журнал (`Ruling:` по каждой). CRITICAL/HIGH —
  чинятся: сначала регрессионный тест (воспроизводит находку, FAIL), потом фикс
  (PASS), `uv run pytest -q` зелёный.
- [ ] **Step 4: Commit** фиксов (если были):

```bash
git commit -am "Аудит среза 24: <суть фиксов>"
```

---

## Срез 25 — маршрут по модулям

### Task 6: Модуль работы, рабочий участок, следующая тема внутри участка

**Files:**
- Modify: `src/llm_tutor/schemas.py` (`RouteStep.closed_at`, `Route.topic_id`, `Route.completed_topics`)
- Modify: `src/llm_tutor/student/route.py`
- Modify: `src/llm_tutor/student/planner.py` (`ready_nodes(scope=)`)
- Test: `tests/test_route.py`, `tests/test_planner.py`

**Interfaces:**
- Consumes: `CourseGraph.topic_of/topic_ids/ancestors` (задача 3).
- Produces (`student/route.py`): `working_section(graph, route, topic_id: int | None)
  -> list[str]`; `topic_finished(graph, route, topic_id: int) -> bool`;
  `choose_topic(graph, route, *, previous_topic: int | None = None) -> int | None`;
  `topic_for(graph, state: SessionState) -> int | None`;
  `section_route(graph, route) -> Route`; `mark_claimed(route, concept_ids) -> Route`;
  `release_current(route) -> Route`; `upcoming(route, first, *, limit, section=None)`;
  `build_route` заполняет `topic_id`, переносит `completed_topics`;
  `next_node_id` выбирает только внутри участка.
  `planner.ready_nodes(..., scope: Collection[str] | None = None)`.

- [ ] **Step 1: Write the failing tests** — в `tests/test_route.py`:

```python
def _course(nodes: dict[str, int], edges: list[tuple[str, str]]) -> CourseGraph:
    """Граф из тем с модулями: {"a": 1, "x": 2}."""
    return CourseGraph(
        [Concept(id=node, name=node, topic_id=topic) for node, topic in nodes.items()],
        [Edge(from_id=src, to_id=dst, hard=True) for src, dst in edges],
    )


def _route(statuses: dict[str, str], **fields) -> Route:
    return Route(
        steps=[RouteStep(concept_id=n, mode="full", status=s) for n, s in statuses.items()],
        **fields,
    )


def test_working_section_is_module_plus_open_ancestors() -> None:
    graph = _course({"a": 1, "b": 1, "x": 2, "y": 2}, [("a", "x"), ("b", "y")])
    route = _route({"a": "closed", "b": "ahead", "x": "ahead", "y": "ahead"})

    assert route_mod.working_section(graph, route, 2) == ["b", "x", "y"]


def test_next_node_stays_inside_current_module(conn, settings) -> None:
    """Лёгкая тема модуля 2 не уводит из модуля 1, пока он не пройден."""
    graph = CourseGraph(
        [
            Concept(id="a", name="a", difficulty=0.9, topic_id=1),
            Concept(id="x", name="x", difficulty=0.1, topic_id=2),
        ],
        [],
    )
    route = _route({"a": "ahead", "x": "ahead"}, topic_id=1)

    assert route_mod.next_node_id(conn, graph, route, now=0.0, settings=settings) == "a"


def test_choose_topic_moves_on_when_module_finished() -> None:
    graph = _course({"a": 1, "x": 2}, [])
    route = _route({"a": "closed", "x": "ahead"})

    assert route_mod.choose_topic(graph, route, previous_topic=1) == 2


def test_choose_topic_keeps_module_for_pulled_in_ancestor() -> None:
    """Незакрытый предок из модуля 1 стал текущим — модуль работы всё ещё 2."""
    graph = _course({"a": 1, "x": 2}, [("a", "x")])
    route = _route({"a": "current", "x": "ahead"})

    assert route_mod.choose_topic(graph, route, previous_topic=2) == 2


def test_choose_topic_prefers_unfinished_module_over_completed_one() -> None:
    graph = _course({"a": 1, "x": 2}, [])
    route = _route({"a": "ahead", "x": "ahead"}, completed_topics=[1])

    assert route_mod.choose_topic(graph, route) == 2


def test_choose_topic_is_none_when_everything_closed() -> None:
    graph = _course({"a": 1}, [])

    assert route_mod.choose_topic(graph, _route({"a": "closed"})) is None


def test_build_route_carries_module_state(conn, settings) -> None:
    graph = _course({"a": 1, "x": 2}, [])
    previous = _route({"a": "closed", "x": "ahead"}, topic_id=2, completed_topics=[1])

    fresh = route_mod.build_route(conn, graph, previous=previous, now=0.0, settings=settings)

    assert fresh.topic_id == 2
    assert fresh.completed_topics == [1]


def test_legacy_snapshot_continues_in_first_module(conn, settings) -> None:
    """Снимок до модулей: только темы topic01, без topic_id — работа в модуле 1."""
    graph = _course({"a": 1, "b": 1, "x": 2}, [("a", "b")])
    legacy = _route({"a": "closed", "b": "current"})

    fresh = route_mod.build_route(
        conn, graph, current_node_id="b", previous=legacy, now=0.0, settings=settings
    )

    assert fresh.topic_id == 1
    assert {s.concept_id: s.status for s in fresh.steps}["a"] == "closed"


def test_mark_claimed_touches_only_ahead() -> None:
    route = _route({"a": "closed", "b": "current", "c": "ahead"})

    marked = route_mod.mark_claimed(route, {"a", "b", "c"})

    assert _statuses(marked) == {"a": "closed", "b": "current", "c": "claimed"}


def test_release_current_makes_it_ahead() -> None:
    assert _statuses(route_mod.release_current(_route({"a": "current"}))) == {"a": "ahead"}


def test_upcoming_respects_section() -> None:
    route = _route({"a": "ahead", "x": "ahead"})

    assert [s.concept_id for s in route_mod.upcoming(route, "a", limit=5, section=["a"])] == ["a"]


def test_topic_for_new_student_is_first_module() -> None:
    graph = _course({"a": 1, "x": 2}, [])

    assert route_mod.topic_for(graph, SessionState()) == 1
```

В `tests/test_planner.py`:

```python
def test_ready_nodes_respects_scope(conn, settings) -> None:
    graph = CourseGraph([Concept(id="a", name="a"), Concept(id="b", name="b")], [])

    ready = planner.ready_nodes(conn, graph, scope={"b"}, now=0.0, settings=settings)

    assert [node.concept_id for node in ready] == ["b"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_route.py tests/test_planner.py -q`
Expected: FAIL — нет `working_section`, `Route` не принимает `topic_id`.

- [ ] **Step 3: Implement**

`schemas.py`:

```python
class RouteStep(BaseModel):
    """Один узел маршрута ученика."""

    concept_id: str
    mode: NodeMode
    status: RouteStepStatus
    # Когда тема закрыта последний раз (unix time). Провалы ПОСЛЕ закрытия
    # открывают её снова, а у открывшейся время остаётся меткой «была закрыта»:
    # по ней тема прошлого модуля попадает в рабочий участок текущего.
    closed_at: float | None = None


class Route(BaseModel):
    """Путь от текущего положения до цели со снимком состояния узлов."""

    goal_concept_id: str | None = None
    steps: list[RouteStep] = Field(default_factory=list)
    # Модуль, над которым идёт работа (None — открытых тем нет нигде).
    topic_id: int | None = None
    # Модули, о прохождении которых ученику уже сказали «🎉»: возврат к их
    # темам не объявляет модуль пройденным повторно.
    completed_topics: list[int] = Field(default_factory=list)
    # (closed_count — без изменений)
```

`planner.ready_nodes` — новый параметр `scope: Collection[str] | None = None`
(импорт `from collections.abc import Collection, Mapping`), в докстринг:
«``scope`` — рабочий участок модуля: кандидаты берутся только из него». Тело
вместо расчёта `means` по всему графу и `candidates`:

```python
    allowed = _scope(graph, goal_concept_id)
    if scope is not None:
        allowed = allowed & set(scope)
    # Владение нужно кандидатам и их пререквизитам — не всему курсу.
    needed = allowed | {p for node_id in allowed for p in graph.prerequisites(node_id)}
    overrides = mastery_overrides or {}
    means = {
        node_id: overrides.get(node_id)
        or beta.estimate(conn, node_id, now=stamp, settings=s)
        for node_id in needed
    }
    candidates = [
        node_id
        for node_id in allowed
        if _eligible(graph, means, s.mastery_verify_threshold, node_id, completed_ids)
    ]
```

(комментарий про переопределения над `overrides` сохранить).

`route.py` — импорт `from collections.abc import Collection, Mapping`. Новые
функции после `_current_of`:

```python
def working_section(
    graph: CourseGraph, route: Route, topic_id: int | None
) -> list[str]:
    """Рабочий участок модуля (§4.1 спеки модулей), в порядке маршрута.

    Темы модуля плюс незакрытые темы прошлых модулей двух видов: предки тем
    модуля (на них он опирается) и темы, открывшиеся снова после закрытия
    (``closed_at`` у незакрытого шага — провал по заданию со вторичным весом
    или просроченное повторение, §4.2): забытое проходится здесь же.
    """
    if topic_id is None:
        return []
    known = [step for step in route.steps if graph.has_node(step.concept_id)]
    own = {step.concept_id for step in known if graph.topic_of(step.concept_id) == topic_id}
    ancestors: set[str] = set()
    for node_id in own:
        ancestors |= graph.ancestors(node_id)
    return [
        step.concept_id
        for step in known
        if step.concept_id in own
        or (
            step.status != "closed"
            and graph.topic_of(step.concept_id) < topic_id
            and (step.concept_id in ancestors or step.closed_at is not None)
        )
    ]


def topic_finished(graph: CourseGraph, route: Route, topic_id: int) -> bool:
    """Пройден ли модуль: все темы его рабочего участка закрыты."""
    status = {step.concept_id: step.status for step in route.steps}
    return all(status[node_id] == "closed" for node_id in working_section(graph, route, topic_id))


def choose_topic(
    graph: CourseGraph, route: Route, *, previous_topic: int | None = None
) -> int | None:
    """Модуль, над которым идёт работа (§4.1 спеки модулей).

    1. Текущая тема задаёт модуль — кроме незакрытого предка из прошлого
       модуля: его подтянул участок модуля, где идёт работа.
    2. Без текущей темы — прежний модуль, пока в его участке есть открытое.
    3. Иначе первый по номеру ещё не пройденный модуль с открытыми темами; если
       открытое осталось только в пройденных (повторение) — первый из них.
    """
    current = _current_of(route)
    if current is not None and graph.has_node(current):
        if previous_topic is not None and current in working_section(
            graph, route, previous_topic
        ):
            return previous_topic
        return graph.topic_of(current)
    if previous_topic is not None and not topic_finished(graph, route, previous_topic):
        return previous_topic
    open_topics = sorted(
        {
            graph.topic_of(step.concept_id)
            for step in route.steps
            if step.status != "closed" and graph.has_node(step.concept_id)
        }
    )
    fresh = [topic for topic in open_topics if topic not in route.completed_topics]
    if fresh:
        return fresh[0]
    return open_topics[0] if open_topics else None


def topic_for(graph: CourseGraph, state: SessionState) -> int | None:
    """Модуль занятия — для анкеты, промпта, RAG и экранов."""
    if state.route is not None:
        if state.route.topic_id is not None:
            return state.route.topic_id
        return choose_topic(graph, state.route)
    if state.current_node_id is not None and graph.has_node(state.current_node_id):
        return graph.topic_of(state.current_node_id)
    return graph.topic_ids[0] if graph.node_ids else None


def section_route(graph: CourseGraph, route: Route) -> Route:
    """Снимок, урезанный до рабочего участка модуля работы (экраны, промпт)."""
    topic = route.topic_id if route.topic_id is not None else choose_topic(graph, route)
    keep = set(working_section(graph, route, topic))
    return route.model_copy(
        update={
            "steps": [step for step in route.steps if step.concept_id in keep],
            "topic_id": topic,
        }
    )


def mark_claimed(route: Route, concept_ids: Collection[str]) -> Route:
    """Помечает заявленным то, что ещё впереди (анкета модуля, §4.1).

    Закрытое и текущее не трогаем: самооценка не отменяет доказанного.
    """
    wanted = set(concept_ids)
    return route.model_copy(
        update={
            "steps": [
                step.model_copy(update={"status": "claimed"})
                if step.concept_id in wanted and step.status == "ahead"
                else step
                for step in route.steps
            ]
        }
    )


def release_current(route: Route) -> Route:
    """Снятая с позиции «текущая» тема снова впереди (урок с чистого листа)."""
    return route.model_copy(
        update={
            "steps": [
                step.model_copy(update={"status": "ahead"}) if step.status == "current" else step
                for step in route.steps
            ]
        }
    )
```

`build_route` — после цикла (пока без правила повторного открытия, оно в задаче 7):

```python
    route = Route(
        goal_concept_id=goal_concept_id,
        steps=steps,
        completed_topics=list(previous.completed_topics) if previous is not None else [],
    )
    previous_topic = previous.topic_id if previous is not None else None
    return route.model_copy(
        update={"topic_id": choose_topic(graph, route, previous_topic=previous_topic)}
    )
```

`next_node_id` — тело:

```python
    s = settings or get_settings()
    stamp = time.time() if now is None else now
    # Снимок до среза 25 модуля не знает — выбираем его по снимку.
    topic = route.topic_id if route.topic_id is not None else choose_topic(graph, route)
    if topic is None:
        return None
    section = set(working_section(graph, route, topic))
    closed = {step.concept_id for step in route.steps if step.status == "closed"}
    claimed = {step.concept_id for step in route.steps if step.status == "claimed"}
    # Заявленное — выполненный пререквизит: урок начинается с первого
    # неуверенного блока, а не с азов, которые ученик назвал знакомыми.
    ready = planner.ready_nodes(
        conn,
        graph,
        goal_concept_id=route.goal_concept_id,
        limit=max(len(section), 1),
        now=stamp,
        settings=s,
        mastery_overrides=mastery_overrides,
        completed_ids=frozenset(closed | claimed),
        scope=section,
    )
    for node in ready:
        if node.concept_id not in closed | claimed:
            return node.concept_id
    # Незаявленное кончилось — пора подтвердить заявленное, по порядку маршрута.
    return next(
        (
            step.concept_id
            for step in route.steps
            if step.status == "claimed" and step.concept_id in section
        ),
        None,
    )
```

В докстринг `next_node_id`: «Выбор — только внутри рабочего участка модуля
работы (§4.1 спеки модулей): так модули идут по порядку».

`upcoming` — сигнатура `upcoming(route, first, *, limit, section: Collection[str] | None = None)`;
первой строкой тела:

```python
    allowed = None if section is None else set(section)
    steps = [s for s in route.steps if allowed is None or s.concept_id in allowed]
```

и дальше `head`/`rest` считать по `steps`, а не `route.steps`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/schemas.py src/llm_tutor/student/route.py src/llm_tutor/student/planner.py tests/test_route.py tests/test_planner.py
git commit -m "Срез 25: модуль работы и рабочий участок — следующая тема внутри модуля"
```

---

### Task 7: Закрытое открывается провалами; пересмотр — только в участке

**Files:**
- Modify: `src/llm_tutor/student/route.py` (`build_route`, `diff_routes`, `refresh`)
- Test: `tests/test_route.py`

**Interfaces:**
- Produces: `REOPEN_FAILURES = 2`; `RouteStep.closed_at` заполняется для закрытых;
  `diff_routes(previous, current, *, within: Collection[str] | None = None)`;
  `refresh` сообщает только об изменениях внутри участка.

- [ ] **Step 1: Write the failing tests**

```python
from llm_tutor.schemas import Event


def _closed_at(route: Route, node: str) -> float | None:
    return next(step.closed_at for step in route.steps if step.concept_id == node)


def test_closed_node_reopens_after_failures_since_closing(conn, settings) -> None:
    graph = _course({"a": 1}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(steps=[RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0)])
    for ts in (11.0, 12.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(conn, graph, previous=previous, now=13.0, settings=settings)

    assert _statuses(fresh)["a"] != "closed"


def test_failures_before_closing_do_not_reopen(conn, settings) -> None:
    graph = _course({"a": 1}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(steps=[RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0)])
    for ts in (5.0, 6.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(conn, graph, previous=previous, now=13.0, settings=settings)

    assert _statuses(fresh)["a"] == "closed"
    assert _closed_at(fresh, "a") == 10.0


def test_legacy_closed_step_gets_closed_at_now(conn, settings) -> None:
    """Снимок до среза 25: время закрытия неизвестно — отсчёт с пересчёта."""
    graph = _course({"a": 1}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=1.0))
    repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=2.0))
    previous = Route(steps=[RouteStep(concept_id="a", mode="full", status="closed")])

    fresh = route_mod.build_route(conn, graph, previous=previous, now=7.0, settings=settings)

    assert _statuses(fresh)["a"] == "closed"
    assert _closed_at(fresh, "a") == 7.0


def test_reopened_ancestor_joins_next_module_section(conn, settings) -> None:
    graph = _course({"a": 1, "x": 2}, [("a", "x")])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0),
            RouteStep(concept_id="x", mode="full", status="ahead"),
        ],
        topic_id=2,
        completed_topics=[1],
    )
    for ts in (11.0, 12.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(conn, graph, previous=previous, now=13.0, settings=settings)

    assert route_mod.working_section(graph, fresh, 2) == ["a", "x"]


def test_reopened_theme_without_edge_joins_current_section(conn, settings) -> None:
    """Провалы по заданию модуля 2 со вторичным весом на «a» — без ребра a → x."""
    graph = _course({"a": 1, "x": 2}, [])
    repos.upsert_concept(conn, Concept(id="a", name="a"))
    previous = Route(
        steps=[
            RouteStep(concept_id="a", mode="full", status="closed", closed_at=10.0),
            RouteStep(concept_id="x", mode="full", status="current"),
        ],
        topic_id=2,
        completed_topics=[1],
    )
    for ts in (11.0, 12.0):
        repos.add_event(conn, Event(source="autotest", result=0.0, concept_id="a", ts=ts))

    fresh = route_mod.build_route(
        conn, graph, current_node_id="x", previous=previous, now=13.0, settings=settings
    )

    assert _closed_at(fresh, "a") == 10.0  # метка «была закрыта» осталась
    assert route_mod.working_section(graph, fresh, 2) == ["a", "x"]
    assert fresh.topic_id == 2


def test_never_closed_theme_of_earlier_module_stays_out(conn, settings) -> None:
    """Прыжок вперёд: незакрытое прошлого модуля без ребра и без метки — не в участке."""
    graph = _course({"a": 1, "x": 2}, [])

    fresh = route_mod.build_route(conn, graph, current_node_id="x", now=0.0, settings=settings)

    assert route_mod.working_section(graph, fresh, 2) == ["x"]


def test_course_growth_is_not_reported_as_route_change(conn, settings) -> None:
    """Снимок topic01 + новые модули в графе — ученику сообщать не о чем."""
    graph = _course({"a": 1, "b": 1, "x": 2, "y": 2}, [("a", "b")])
    legacy = _route({"a": "closed", "b": "current"})
    state = SessionState(current_node_id="b", route=legacy)

    _, note = route_mod.refresh(conn, state, graph, now=0.0, settings=settings)

    assert note is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_route.py -q`
Expected: FAIL — тема не открывается, `closed_at` пуст, `refresh` сообщает «добавилось x, y».

- [ ] **Step 3: Implement** — в `route.py`:

```python
# Столько провалов ПОСЛЕ закрытия темы открывают её снова (§4.2 спеки модулей).
REOPEN_FAILURES = 2


def _failures_since(conn: sqlite3.Connection, concept_id: str, since: float) -> int:
    """Неудачные свидетельства по теме позже момента ``since``."""
    return sum(
        1
        for event in repos.get_events(conn, concept_id)
        if event.ts is not None and event.ts > since and event.result < 0.5
    )
```

В `build_route` вместо `closed_before = {...}` — словарь прошлых шагов, и
цикл:

```python
    before = {step.concept_id: step for step in (previous.steps if previous else [])}
    ...
    for concept_id in _scope(graph, goal_concept_id):
        mode = planner.mode_for_node(conn, graph, concept_id, now=stamp, settings=s)
        old = before.get(concept_id)
        was_closed = old is not None and old.status == "closed"
        # Снимок до среза 25 времени закрытия не хранит: отсчёт — с этого пересчёта.
        closed_at = old.closed_at if was_closed and old.closed_at is not None else stamp
        # Закрытое остаётся закрытым, пока владение держится: просроченное
        # повторение (§6.4) или провалы после закрытия — например, по заданиям
        # следующих модулей, которые на тему опираются, — возвращают её.
        stays_closed = (
            was_closed
            and mode != "review"
            and _failures_since(conn, concept_id, closed_at) < REOPEN_FAILURES
        )
        if mode == "skip" or stays_closed:
            status = "closed"
        elif concept_id == current_node_id:
            status = "current"
        elif concept_id in claimed_before:
            status = "claimed"
        else:
            status = "ahead"
        # Открывшаяся снова тема хранит время прошлого закрытия — метку для
        # рабочего участка; никогда не закрытая — без метки.
        if status == "closed" or was_closed:
            mark = closed_at
        else:
            mark = old.closed_at if old is not None else None
        steps.append(
            RouteStep(concept_id=concept_id, mode=mode, status=status, closed_at=mark)
        )
```

Докстринг `build_route` дополнить абзацем про `REOPEN_FAILURES`.

`diff_routes`:

```python
def diff_routes(
    previous: Route | None, current: Route, *, within: Collection[str] | None = None
) -> RouteChanges:
    """Сравнивает снимок с новым расчётом; ``within`` — только эти темы."""
    keep = None if within is None else set(within)

    def _steps(route: Route | None) -> dict[str, RouteStep]:
        return {
            step.concept_id: step
            for step in (route.steps if route is not None else [])
            if keep is None or step.concept_id in keep
        }

    old, new = _steps(previous), _steps(current)
    # (дальше — прежний расчёт closed/added/removed/current_changed по old/new)
```

`refresh`:

```python
    changes = diff_routes(
        state.route, fresh, within=working_section(graph, fresh, fresh.topic_id)
    )
```

с комментарием: «Ученику важен участок модуля: рост курса и чужие модули —
не пересмотр его плана».

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/route.py tests/test_route.py
git commit -m "Срез 25: закрытое открывается провалами, пересмотр — в участке модуля"
```

---

### Task 8: Переход между модулями в ходе урока

**Files:**
- Modify: `src/llm_tutor/core/turn.py` (`_close_node_if_ready`, `handle_turn`, константы)
- Modify: `tests/course_fixtures.py` (`finish_all_but`)
- Test: `tests/test_modules_turn.py` (новый), `tests/test_turn.py`

**Interfaces:**
- Consumes: `choose_topic`, `topic_finished`, `next_node_id` (задачи 6–7),
  `repos.get_topic` (задача 2).
- Produces: константы `TOPIC_DONE_NOTE`, `NEXT_TOPIC_NOTE`, `COURSE_DONE_NOTE`,
  новый текст `ROUTE_DONE_REPLY`; `_close_node_if_ready` отмечает
  `completed_topics`, ставит `route.topic_id` следующего модуля; после хода,
  закрывшего модуль без следующей темы, задание не выдаётся.
  `course_fixtures.finish_all_but(conn, last, *, topic_id, completed=()) -> Item`,
  `course_fixtures.enter_module(conn, topic_id, *, completed=()) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/course_fixtures.py` — дописать:

```python
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.schemas import Route, RouteStep, SessionState


def finish_all_but(conn, last: str, *, topic_id: int, completed: tuple[int, ...] = ()) -> Item:
    """Всё закрыто, кроме ``last``: по ней висит задание и серия 1.

    Темы модулей после модуля ``last`` — впереди. Возвращает висящее задание.
    """
    graph = CourseGraph.load(conn)
    boundary = graph.topic_of(last)
    steps = []
    for node in graph.topo_order():
        if node == last:
            steps.append(RouteStep(concept_id=node, mode="full", status="current"))
        elif graph.topic_of(node) <= boundary:
            steps.append(RouteStep(concept_id=node, mode="full", status="closed", closed_at=0.5))
        else:
            steps.append(RouteStep(concept_id=node, mode="full", status="ahead"))
    item = next(
        i for i in repos.get_items(conn)
        if i.answer_type in ("choice", "short") and last in i.concept_weights
    )
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(
            current_node_id=last,
            phase="practice",
            node_streak=1,
            pending_item_id=item.id,
            route=Route(steps=steps, topic_id=topic_id, completed_topics=list(completed)),
        ),
    )
    return item


def enter_module(conn, topic_id: int, *, completed: tuple[int, ...] = ()) -> None:
    """Модули до ``topic_id`` закрыты целиком; ученик на входе в ``topic_id``."""
    graph = CourseGraph.load(conn)
    steps = [
        RouteStep(concept_id=node, mode="full", status="closed", closed_at=0.5)
        if graph.topic_of(node) < topic_id
        else RouteStep(concept_id=node, mode="full", status="ahead")
        for node in graph.topo_order()
    ]
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(
        conn,
        session_id,
        SessionState(
            route=Route(steps=steps, topic_id=topic_id, completed_topics=list(completed))
        ),
    )
```

`tests/test_modules_turn.py`:

```python
"""Переход между модулями в ходе урока (срез 25)."""

from course_fixtures import correct_answer, finish_all_but, load_two_modules
from fakes import GradingTutor
from llm_tutor.core.turn import COURSE_DONE_NOTE, handle_turn
from llm_tutor.db import repos


def _state(conn):
    return repos.get_session_state(conn, repos.get_open_session(conn))


def _module_two_survey_done(conn) -> None:
    for key in ("t02_level", "t02_basics", "t02_relations"):
        repos.set_fact(conn, key, "Знаю в теории", source="self")


async def test_closing_last_topic_of_module_moves_to_next(conn, settings) -> None:
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "🎉 Модуль 1" in reply.text
    assert "Дальше — модуль 2" in reply.text
    state = _state(conn)
    assert state.route.completed_topics == [1]
    assert state.route.topic_id == 2
    assert state.current_node_id == "mini_plots"
    assert state.pending_item_id is not None and state.pending_item_id >= 2000


async def test_returning_to_completed_module_has_no_fanfare(conn, settings) -> None:
    """Возврат к теме пройденного модуля: без повторного «🎉», дальше — модуль 2."""
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1, completed=(1,))

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "🎉" not in reply.text
    assert _state(conn).route.topic_id == 2


async def test_last_topic_of_course_says_course_done_once(conn, settings) -> None:
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "mini_corr", topic_id=2, completed=(1,))

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "🎉 Модуль 2" in reply.text
    assert reply.text.count("курс пройден") == 1
    assert COURSE_DONE_NOTE in reply.text
    assert reply.tail is None
    state = _state(conn)
    assert state.current_node_id is None and state.pending_item_id is None
    assert state.route.topic_id is None


async def test_legacy_snapshot_does_not_end_course_early(conn, settings) -> None:
    """Снимок до среза 25 (только темы topic01): конец модуля 1 — не конец курса."""
    load_two_modules(conn)
    _module_two_survey_done(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    legacy = Route(
        steps=[
            s.model_copy(update={"closed_at": None})
            for s in state.route.steps
            if not s.concept_id.startswith("mini_")
        ]
    )
    repos.update_session_state(conn, session_id, state.model_copy(update={"route": legacy}))

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "курс пройден" not in reply.text
    assert "Дальше — модуль 2" in reply.text
```

(импорт `from llm_tutor.schemas import Route` в `tests/test_modules_turn.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_modules_turn.py -q`
Expected: FAIL — нет `COURSE_DONE_NOTE`, нет «🎉».

- [ ] **Step 3: Implement** — в `core/turn.py`:

Константы (вместо старого `ROUTE_DONE_REPLY`):

```python
# Курс пройден: открытых тем не осталось ни в одном модуле (§4.3 спеки модулей).
ROUTE_DONE_REPLY = "🎓 Курс пройден — открытых тем не осталось. Свериться можно в /plan."
# Модуль пройден: все темы его рабочего участка закрыты.
TOPIC_DONE_NOTE = "🎉 Модуль {number} «{title}» пройден!"
# Переход в следующий модуль: с какой темы начинаем.
NEXT_TOPIC_NOTE = "Дальше — модуль {number} «{title}», начинаем с «{name}»."
# Закрыта последняя открытая тема курса.
COURSE_DONE_NOTE = "🎓 Это был последний шаг — курс пройден! Свериться можно в /plan."
```

Хелпер рядом с `_is_claimed`:

```python
def _topic_title(conn: sqlite3.Connection, topic_id: int) -> str:
    """Название модуля для объявлений (без модуля в БД — его номер)."""
    topic = repos.get_topic(conn, topic_id)
    return topic.title if topic is not None else f"№{topic_id}"
```

`_close_node_if_ready` — всё после `closed_name = graph.concept(node_id).name`:

```python
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
            "route": route,
        }
    )
    if next_node_id is None:
        notes.append(COURSE_DONE_NOTE)
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
```

`handle_turn` — перед `if passed:` в ветке ответа добавить `closed_note: str | None = None`;
блок «Ведём дальше» заменить:

```python
        # Ведём дальше: следующий узел после закрытия или ещё задание по этому.
        # Тема закрыта, а следующей нет (курс пройден; со среза 26 — ещё и
        # анкета нового модуля) — задание выдавать не по чему.
        if closed_note and new_state.current_node_id is None:
            task_text, options = "", None
        else:
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
        # Задание уходит отдельным сообщением: … (комментарий без изменений)
        tail = task_text if new_state.pending_item_id is not None else None
        if tail is None and task_text:
            reply = f"{reply}\n\n{task_text}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное (`test_turn.py::test_route_done_hands_no_task` ищет «пройден» —
новый текст его содержит; `test_resume_reports_finished_route` сравнивает с константой).

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/core/turn.py tests/course_fixtures.py tests/test_modules_turn.py
git commit -m "Срез 25: переход между модулями — «🎉 модуль пройден» и «🎓 курс пройден»"
```

---

### Task 9: Экраны маршрута по участку модуля

**Files:**
- Modify: `src/llm_tutor/bot/render.py` (`render_plan`, `render_status`, `_module_label`)
- Modify: `src/llm_tutor/bot/start.py` (`begin_lesson`: участок для списка шагов)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `section_route`, `working_section`, `upcoming(section=)` (задача 6).
- Produces: `/plan` — шапка «🗺 Маршрут · Модуль N/M · название — пройдено X из Y» и
  только темы участка; курс пройден — «🎓 Курс пройден — все модули закрыты.»;
  `/status` — строка «Модуль: …» и прогресс по участку.

- [ ] **Step 1: Write the failing tests** — в `tests/test_render.py`
  (импорты `from course_fixtures import finish_all_but, load_two_modules`):

```python
def test_render_plan_shows_only_current_module(conn, settings) -> None:
    load_two_modules(conn)
    repos.ensure_open_session(conn, now=1.0)

    text = render_plan(conn, now=0.0, settings=settings)

    assert "Модуль 1/2 · Первичный анализ данных с Pandas" in text
    assert "Гистограмма" not in text  # тема модуля 2 не в участке модуля 1


def test_render_plan_after_course_end(conn, settings) -> None:
    load_two_modules(conn)
    finish_all_but(conn, "mini_corr", topic_id=2, completed=(1,))
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    closed = [s.model_copy(update={"status": "closed", "closed_at": 0.5}) for s in state.route.steps]
    repos.update_session_state(
        conn,
        session_id,
        state.model_copy(
            update={
                "current_node_id": None,
                "pending_item_id": None,
                "route": state.route.model_copy(update={"steps": closed, "completed_topics": [1, 2]}),
            }
        ),
    )

    assert "Курс пройден" in render_plan(conn, now=1.0, settings=settings)


def test_render_status_names_module(conn, settings) -> None:
    load_two_modules(conn)
    repos.ensure_open_session(conn, now=1.0)

    assert "Модуль: 1/2" in render_status(conn, now=0.0, settings=settings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_render.py -q`
Expected: FAIL — в плане нет «модуль 1/2», темы модуля 2 видны.

- [ ] **Step 3: Implement** — в `render.py` (импорт `from llm_tutor.db import repos`,
  если его нет):

```python
def _module_label(conn: sqlite3.Connection, topic_id: int) -> str:
    """«N/M · название» — готовый HTML (без модуля в БД — просто номер)."""
    topics = repos.get_topics(conn)
    topic = next((t for t in topics if t.number == topic_id), None)
    if topic is None:
        return str(topic_id)
    return f"{topic_id}/{len(topics)} · {escape(topic.title)}"
```

`render_plan` — после построения `route` и проверки `if not route.steps`:

```python
    if route.topic_id is None:
        return "🎓 Курс пройден — все модули закрыты."
    section = route_mod.section_route(graph, route)
    header = (
        f"🗺 <b>Маршрут</b> · Модуль {_module_label(conn, route.topic_id)} — "
        f"пройдено {section.closed_count} из {len(section.steps)}"
    )
    return f"{header}\n{render_steps(graph, section.steps)}"
```

(старую ветку `route.closed_count == len(route.steps)` удалить — её заменяет
`topic_id is None`).

`render_status` — блок маршрута:

```python
    if route.steps:
        if route.topic_id is None:
            lines.append("Маршрут: курс пройден 🎓")
        else:
            section = route_mod.section_route(graph, route)
            lines.append(f"Модуль: {_module_label(conn, route.topic_id)}")
            lines.append(f"Маршрут: пройдено {section.closed_count} из {len(section.steps)}")
```

`bot/start.py` `begin_lesson` — список шагов по участку модуля первой темы:

```python
    first = route_mod.next_node_id(conn, graph, route, settings=settings)
    section = (
        route_mod.working_section(graph, route, graph.topic_of(first))
        if first is not None
        else []
    )
    upcoming = route_mod.upcoming(route, first, limit=render.PLAN_STEPS, section=section)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/bot/render.py src/llm_tutor/bot/start.py tests/test_render.py
git commit -m "Срез 25: /plan, /status и список шагов — по участку модуля"
```

---

### Task 10: Аудит среза 25 и замер скорости

- [ ] **Step 1: Замер.** Инструментом Write сохранить в scratch-каталог
  `measure_route.py`:

```python
"""Замер: маршрут и выбор темы на синтетическом курсе 10×20 (срез 25)."""

import time

from llm_tutor.config import Settings
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db.connection import get_conn, migrate
from llm_tutor.db import repos
from llm_tutor.schemas import Concept, Edge, Event, Route, RouteStep, SessionState
from llm_tutor.student import route

settings = Settings(_env_file=None, openrouter_api_key="k", telegram_bot_token="t")
nodes = [Concept(id=f"m{t}_{i}", name="n", topic_id=t) for t in range(1, 11) for i in range(20)]
edges = [Edge(from_id=f"m{t}_{i}", to_id=f"m{t}_{i + 1}") for t in range(1, 11) for i in range(19)]
edges += [Edge(from_id=f"m{t}_19", to_id=f"m{t + 1}_0", hard=False) for t in range(1, 10)]
graph = CourseGraph(nodes, edges)
conn = get_conn(":memory:")
migrate(conn)
# Нагрузка как у ученика в середине курса: журнал событий и владение по каждой теме.
repos.replace_seed(conn, nodes, edges, [])
for index, node in enumerate(nodes):
    for k in range(15):
        repos.add_event(
            conn, Event(source="autotest", result=float(k % 3 != 0), concept_id=node.id, ts=k)
        )
    repos.upsert_mastery(conn, node.id, alpha=6.0, beta=2.0, last_seen=10.0)
# Первые 5 модулей закрыты — снимок как после полкурса.
closed = Route(
    steps=[
        RouteStep(concept_id=n.id, mode="full", status="closed", closed_at=5.0)
        for n in nodes
        if n.topic_id <= 5
    ],
    topic_id=6,
    completed_topics=[1, 2, 3, 4, 5],
)
state = SessionState(route=closed)
start = time.perf_counter()
fresh, _ = route.refresh(conn, state, graph, now=20.0, settings=settings)
route.next_node_id(conn, graph, fresh, now=20.0, settings=settings)
print(f"refresh + next_node_id: {time.perf_counter() - start:.3f} s")
```

Run: `uv run python <scratch>/measure_route.py`
Expected: меньше 0.5 с (ход зовёт `refresh` до трёх раз). Результат — в журнал. Больше 0.5 с — находка HIGH в
триаж аудита (кандидат на фикс: `mode_for_node` считает владение мягких
пререквизитов заново для каждой темы — кешировать `beta.estimate` в рамках
одного `build_route`).

- [ ] **Step 2:** Адверсариальное ревью по **Приложению Б**: срез 25, задачи 6–9,
  фокус — Review Focus 1, 4, 5; модуль работы при прыжках вперёд/назад; снимок
  до среза 25 из копии рабочей БД (`state.route` реального ученика → `refresh`
  → нет сообщения о пересмотре, `topic_id == 1`, закрытое закрыто).
- [ ] **Step 3:** Триаж, регрессионные тесты, фиксы, `uv run pytest -q`.
- [ ] **Step 4: Commit** `Аудит среза 25: …`.

---

## Срез 26 — анкета модуля и вход в модуль

### Task 11: Анкета по конфигу модуля

**Files:**
- Modify (переписать): `src/llm_tutor/student/survey.py`
- Modify: `src/llm_tutor/bot/survey.py` (`question_view`, `summary_text`, `_progress`, `_finish`, `_orphan_click`, `on_survey_click`)
- Modify: `src/llm_tutor/core/context.py` (`self_assessment`)
- Modify: `src/llm_tutor/bot/handlers.py` (гейт анкеты)
- Modify: тесты — `tests/test_survey.py`, `tests/test_survey_view.py`,
  `tests/test_bot_flows.py`, `tests/test_chat_action.py`, `tests/test_e2e_topic01.py`,
  `tests/test_route.py`, `tests/test_course.py`
- Test: `tests/test_survey.py`

**Interfaces:**
- Consumes: `SurveyConfig`, `repos.get_topic/get_topics`, `course_fixtures.T1/T2`.
- Produces (`student/survey.py`): `Progress(config: SurveyConfig, given=())`;
  `asked_blocks(config, level)`; `options_for(config, key)`;
  `config_for(conn, topic_id) -> SurveyConfig | None`;
  `apply_answers(conn, config, answers, *, level=None, now=None, settings=None)`;
  `is_completed(conn, config) -> bool`;
  `claimed_concepts(conn, config: SurveyConfig | None = None) -> frozenset[str]`
  (`None` — по всем модулям); `self_assessment(conn, config) -> dict[str, str]`;
  `block_level(conn, concept_id) -> str | None`. Константы `SELF_LEVELS`,
  `PRIOR_LEVELS`, `UNKNOWN_INDEX`, `CONFIDENT_INDEX`, `LEVEL_FROM_SCRATCH`,
  `LEVEL_SOME`, `LEVEL_CONFIDENT`, `GOAL_CONCEPT_KEY` остаются; `BLOCKS`,
  `LEVEL_KEY`, `LEVEL_QUESTION`, `LEVEL_OPTIONS`, `ASSUMED_BY_CONFIDENT`,
  `FOUNDATION`, `Block`, `block_by_key` удаляются.
  `bot/survey.summary_text(config, answers)`.

- [ ] **Step 1: Перевести тесты на конфиг модуля.** Найти вхождения:

```bash
grep -rnE "survey\.(BLOCKS|LEVEL_QUESTION|LEVEL_OPTIONS|LEVEL_KEY|ASSUMED_BY_CONFIDENT|FOUNDATION|block_by_key|is_completed|apply_answers|self_assessment)|Progress\(\)|summary_text\(" tests
```

и заменить по таблице (в каждом затронутом файле — `from course_fixtures import T1`):

| Было | Стало |
|---|---|
| `survey.BLOCKS` | `T1.blocks` |
| `survey.LEVEL_QUESTION` | `T1.level_question` |
| `survey.LEVEL_OPTIONS` | `T1.level_options` |
| `survey.LEVEL_KEY` | `T1.level_key` |
| `survey.ASSUMED_BY_CONFIDENT` | `T1.assumed_by_confident` |
| `survey.FOUNDATION` | `T1.foundation` |
| `survey.block_by_key(k)` | `T1.block(k)` |
| `Progress()` | `Progress(T1)` |
| `survey.is_completed(conn)` | `survey.is_completed(conn, T1)` |
| `survey.apply_answers(conn, answers, …)` | `survey.apply_answers(conn, T1, answers, …)` |
| `survey.self_assessment(conn)` | `survey.self_assessment(conn, T1)` |
| `summary_text(answers)` | `summary_text(T1, answers)` |

В `tests/test_course.py` удалить `test_topic01_seed_carries_its_survey_verbatim`
(константы, с которыми он сверял, уходят; ключи стережёт
`test_topic01_survey_keys_never_change`).

Новые тесты в `tests/test_survey.py` (импорт `from course_fixtures import T1, T2, load_two_modules`):

```python
def test_progress_follows_module_config() -> None:
    progress = Progress(T2)

    assert progress.next_key() == "t02_level"
    progress, asked = _walk(progress.answer(survey.LEVEL_CONFIDENT), 1)
    assert asked == ["t02_relations"]
    assert progress.final_answers() == {"t02_basics": survey.CONFIDENT_INDEX, "t02_relations": 1}


def test_config_for_reads_module_from_db(conn) -> None:
    load_two_modules(conn)

    assert survey.config_for(conn, 2) == T2
    assert survey.config_for(conn, 9) is None


def test_completion_is_per_module(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)

    assert survey.is_completed(conn, T1) is True
    assert survey.is_completed(conn, T2) is False


def test_claimed_concepts_by_module(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T2, {"t02_basics": 3, "t02_relations": 0}, settings=settings)

    assert survey.claimed_concepts(conn, T2) == {"mini_plots", "mini_hist"}
    assert survey.claimed_concepts(conn, T1) == frozenset()
    assert survey.claimed_concepts(conn) == {"mini_plots", "mini_hist"}


def test_block_level_finds_module_block(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T2, {"t02_basics": 1, "t02_relations": 2}, settings=settings)

    assert survey.block_level(conn, "mini_corr") == survey.SELF_LEVELS[2]


def test_self_assessment_is_module_only(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)
    survey.apply_answers(conn, T2, {"t02_basics": 2, "t02_relations": 2}, settings=settings)

    assert set(survey.self_assessment(conn, T2)) == {"Простые графики", "Распределения и связи"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q`
Expected: FAIL — `Progress()` принимает конфиг, `config_for` нет.

- [ ] **Step 3: Implement** — `student/survey.py` целиком:

```python
"""Анкета модуля: профиль в ``facts`` + слабый априор (срезы 22, 26).

Модель ученика стартует с априора Beta(1,1) — «не знаем». Анкета не заменяет
диагностику, а даёт лишь слабые свидетельства ``source='self'``: их вес мал
(``self_evidence_weight``), чтобы самооценка не подменяла реальные задания.

Анкета адаптивная (срез 22): сначала общий уровень, потом только нужные
вопросы по блокам. С среза 26 у каждого модуля своя анкета — вопросы и блоки
лежат в seed модуля (``schemas.SurveyConfig``); здесь только правила ветвления,
чистые функции без aiogram.
"""

import sqlite3
import time
from dataclasses import dataclass

from llm_tutor.config import Settings, get_settings
from llm_tutor.db import repos
from llm_tutor.schemas import SurveyBlock, SurveyConfig
from llm_tutor.student import self_report

# Ключ факта цели: анкетой не заполняется, но маршрут его читает.
GOAL_CONCEPT_KEY = "goal_concept_id"

# Градации самооценки по блоку — общие для всех модулей; короткие, чтобы
# влезать в сетку 2×2. Подпись «Уверенно» менять нельзя: по ней узнаются
# заявленные блоки (в т.ч. у прошедших анкету раньше).
SELF_LEVELS: tuple[str, ...] = (
    "Впервые вижу",
    "Знаю в теории",
    "С подсказками",
    "Уверенно",
)
# Во что превращается индекс ответа: 0 — «впервые вижу», 3 — «уверенно».
PRIOR_LEVELS: tuple[float, ...] = (0.0, 0.3, 0.65, 1.0)
UNKNOWN_INDEX = 0
CONFIDENT_INDEX = 3

# Варианты вопроса об общем уровне — по индексу; тексты у каждого модуля свои.
LEVEL_FROM_SCRATCH, LEVEL_SOME, LEVEL_CONFIDENT = 0, 1, 2


def asked_blocks(config: SurveyConfig, level: int) -> tuple[SurveyBlock, ...]:
    """Блоки, о которых спрашиваем на ветке общего уровня ``level``."""
    if level == LEVEL_FROM_SCRATCH:
        return ()
    if level == LEVEL_CONFIDENT:
        return tuple(
            block for block in config.blocks if block.key not in config.assumed_by_confident
        )
    return config.blocks


def options_for(config: SurveyConfig, key: str) -> tuple[str, ...]:
    """Варианты ответа на вопрос с ключом ``key``."""
    return config.level_options if key == config.level_key else SELF_LEVELS


@dataclass(frozen=True)
class Progress:
    """Ход анкеты модуля: ответы в том порядке, в котором их дали (для «Назад»).

    Неизменяемый: каждый ответ и «Назад» дают новый объект — FSM хранит
    ``given``, а не мутирует общее состояние.
    """

    config: SurveyConfig
    given: tuple[tuple[str, int], ...] = ()

    @property
    def step(self) -> int:
        """Сколько ответов дано — номер текущего вопроса (с нуля)."""
        return len(self.given)

    @property
    def level(self) -> int | None:
        """Ответ на вопрос об общем уровне (``None`` — ещё не дан)."""
        return dict(self.given).get(self.config.level_key)

    @property
    def answers(self) -> dict[str, int]:
        """Ответы по блокам, данные учеником (без достроенных)."""
        return {key: index for key, index in self.given if key != self.config.level_key}

    def next_key(self) -> str | None:
        """Ключ следующего вопроса; ``None`` — анкета кончилась."""
        level = self.level
        if level is None:
            return self.config.level_key
        answers = self.answers
        if any(answers.get(key) == UNKNOWN_INDEX for key in self.config.foundation):
            return None
        for block in asked_blocks(self.config, level):
            if block.key not in answers:
                return block.key
        return None

    def answer(self, index: int) -> "Progress":
        """Новый ход анкеты с ответом ``index`` на текущий вопрос."""
        key = self.next_key()
        if key is None:
            raise ValueError("Анкета уже кончилась — отвечать не на что")
        if not 0 <= index < len(options_for(self.config, key)):
            raise ValueError(f"Нет варианта {index} у вопроса {key!r}")
        return Progress(self.config, (*self.given, (key, index)))

    def back(self) -> "Progress":
        """Новый ход анкеты без последнего ответа."""
        return Progress(self.config, self.given[:-1])

    def position(self) -> tuple[int, int] | None:
        """(номер, всего) для вопроса по блоку; у вопроса об уровне — ``None``."""
        key = self.next_key()
        if key is None or key == self.config.level_key:
            return None
        return len(self.answers) + 1, len(asked_blocks(self.config, self.level))

    def final_answers(self) -> dict[str, int]:
        """Ответы на все блоки модуля: данные учеником плюс достроенные правилом."""
        answers = self.answers
        result: dict[str, int] = {}
        for block in self.config.blocks:
            if block.key in answers:
                result[block.key] = answers[block.key]
            elif self.level == LEVEL_CONFIDENT and block.key in self.config.assumed_by_confident:
                result[block.key] = CONFIDENT_INDEX
            else:
                # «С нуля» или ранний выход на фундаменте — блок незнаком.
                result[block.key] = UNKNOWN_INDEX
        return result


def config_for(conn: sqlite3.Connection, topic_id: int) -> SurveyConfig | None:
    """Анкета модуля из БД (``None`` — такого модуля нет)."""
    topic = repos.get_topic(conn, topic_id)
    return topic.survey if topic is not None else None


def apply_answers(
    conn: sqlite3.Connection,
    config: SurveyConfig,
    answers: dict[str, int],
    *,
    level: int | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> None:
    """Сохраняет ответы анкеты модуля в ``facts`` и раздаёт слабый априор.

    ``answers`` — ключ блока → индекс в ``SELF_LEVELS``; ``level`` — ответ на
    вопрос об общем уровне (только факт, априора не даёт). Повторное
    применение факты перезаписывает, но априор **не** начисляет заново:
    иначе самооценка накрутила бы счётчики.
    """
    s = settings or get_settings()
    stamp = time.time() if now is None else now

    if level is not None:
        if not 0 <= level < len(config.level_options):
            raise ValueError(f"Нет варианта {level} у вопроса об уровне")
        repos.set_fact(conn, config.level_key, config.level_options[level], source="self")

    for key, index in answers.items():
        block = config.block(key)
        if not 0 <= index < len(SELF_LEVELS):
            raise ValueError(f"Нет варианта {index} у блока {key!r}")
        # Априор блока начисляем, только когда ответ на него звучит впервые:
        # иначе повторное прохождение анкеты накрутило бы самооценку.
        fresh = repos.get_fact(conn, key) is None
        repos.set_fact(conn, key, SELF_LEVELS[index], source="self")
        if fresh:
            for concept_id in block.concepts:
                self_report.apply(
                    conn,
                    concept_id,
                    correct=PRIOR_LEVELS[index],
                    now=stamp,
                    settings=s,
                    commit=False,
                )
    conn.commit()


def is_completed(conn: sqlite3.Connection, config: SurveyConfig) -> bool:
    """Прошёл ли ученик анкету модуля: ответы есть на все её блоки."""
    return all(repos.get_fact(conn, block.key) is not None for block in config.blocks)


def claimed_concepts(
    conn: sqlite3.Connection, config: SurveyConfig | None = None
) -> frozenset[str]:
    """Темы блоков, которые ученик назвал знакомыми («Уверенно»).

    ``config`` — один модуль; ``None`` — все модули курса. Самооценка тему не
    закрывает: маршрут пропускает её вперёд и проверяет проходом (срез 23).
    """
    configs = (
        [config] if config is not None else [topic.survey for topic in repos.get_topics(conn)]
    )
    confident = SELF_LEVELS[CONFIDENT_INDEX]
    return frozenset(
        concept_id
        for cfg in configs
        for block in cfg.blocks
        if repos.get_fact(conn, block.key) == confident
        for concept_id in block.concepts
    )


def self_assessment(conn: sqlite3.Connection, config: SurveyConfig) -> dict[str, str]:
    """Самооценка по блокам модуля для промпта: название блока → ответ анкеты."""
    return {
        block.title: value
        for block in config.blocks
        if (value := repos.get_fact(conn, block.key)) is not None
    }


def block_level(conn: sqlite3.Connection, concept_id: str) -> str | None:
    """Ответ анкеты по блоку, в который входит тема (``None`` — не отвечал)."""
    for topic in repos.get_topics(conn):
        for block in topic.survey.blocks:
            if concept_id in block.concepts:
                return repos.get_fact(conn, block.key)
    return None
```

`bot/survey.py`:
- `question_view(progress)`: `progress.config.level_key` вместо `survey.LEVEL_KEY`,
  `progress.config.level_question` и `progress.config.level_options` вместо
  констант, `progress.config.block(key)` вместо `survey.block_by_key(key)`.
- `summary_text(config: SurveyConfig, answers)` — перебор `config.blocks`.
- Пока модуль в FSM не хранится (задача 12) — анкета модуля 1: в
  `make_survey_router` хелпер `_config() -> SurveyConfig` =
  `survey.config_for(conn, 1)`; `_progress(data, config)` строит
  `survey.Progress(config, given)`; на `GO` и в `_orphan_click` —
  `survey.Progress(_config())`; `_finish` зовёт
  `survey.apply_answers(conn, progress.config, answers, level=…)` и
  `summary_text(progress.config, answers)`; в `_orphan_click`
  `survey.is_completed(conn, _config())`.

`core/context.py`: `survey.self_assessment(conn, cfg) if (cfg := survey.config_for(conn, 1)) else {}`
(временно модуль 1; задача 16 берёт модуль занятия).

`bot/handlers.py`: импорт `from llm_tutor.student import survey` вместо
`is_completed as survey_completed`; хелпер внутри `make_router`:

```python
    def _survey_pending() -> bool:
        """Анкета модуля 1 не пройдена (до задачи 12 — единственная анкета)."""
        config = survey.config_for(conn, 1)
        return config is not None and not survey.is_completed(conn, config)
```

и `if not survey_completed(conn)` → `if _survey_pending()` (в `on_start`, `on_text`).

Гейт теперь живёт только при загруженном курсе (анкета берётся из БД). Найти
тесты, где курс не загружен, а ждут приветствие анкеты:
`grep -n "INTRO_TEXT\|LEVEL_QUESTION\|level_question" tests/test_bot_flows.py tests/test_handlers.py` —
в каждом таком тесте без `load_seed(conn)` добавить его первой строкой.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/student/survey.py src/llm_tutor/bot/survey.py src/llm_tutor/core/context.py src/llm_tutor/bot/handlers.py tests
git commit -m "Срез 26: анкета по конфигу модуля из seed"
```

---

### Task 12: Вход в модуль через его анкету

**Files:**
- Modify: `src/llm_tutor/bot/start.py` (`pending_survey_topic`, `module_intro_text`, `begin_lesson`)
- Modify: `src/llm_tutor/bot/survey.py` (`start_survey`, `intro_view`, модуль в FSM, `_finish`)
- Modify: `src/llm_tutor/bot/handlers.py` (гейт по модулю, анкета после хода, `on_answer(state)`)
- Modify: `src/llm_tutor/core/turn.py` (`reset_lesson(claimed=)`, `_close_node_if_ready` — анкета следующего модуля)
- Test: `tests/test_bot_flows.py`, `tests/test_modules_turn.py`

**Interfaces:**
- Consumes: `survey.config_for/is_completed/claimed_concepts` (задача 11),
  `route_mod.topic_for/mark_claimed/release_current/working_section` (задача 6).
- Produces: `start.pending_survey_topic(conn) -> Topic | None`;
  `start.module_intro_text(topic) -> str` (сырой текст);
  `start.begin_lesson(message, state, conn, client, model, settings, *, claimed=(),
  start_node_id=None)`; `survey_bot.start_survey(message, state, topic: Topic | None = None,
  *, then_node: str | None = None)` (FSM: `topic`, `then`; `None` — модуль 1);
  `turn.reset_lesson(conn, *, claimed: Collection[str] = (), now=None)` (снимок
  сохраняется); `turn.NEXT_TOPIC_SURVEY_NOTE`.

- [ ] **Step 1: Write the failing tests**

В `tests/test_modules_turn.py`:

```python
from llm_tutor.core.turn import NEXT_TOPIC_SURVEY_NOTE


async def test_next_module_without_survey_waits_for_it(conn, settings) -> None:
    """Модуль 1 пройден, анкета модуля 2 не пройдена — задания нет, ждём анкету."""
    load_two_modules(conn)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)

    reply = await handle_turn(
        conn, GradingTutor(conn, passed=True), "m", correct_answer(item), now=2.0, settings=settings
    )

    assert "Дальше — модуль 2" in reply.text
    assert "пара вопросов" in reply.text
    state = _state(conn)
    assert state.current_node_id is None and state.pending_item_id is None
    assert state.route.topic_id == 2
```

В `tests/test_bot_flows.py` (импорты `from course_fixtures import T1, T2,
correct_answer, enter_module, finish_all_but, load_two_modules`,
`from fakes import GradingTutor`, `from llm_tutor.bot import start`,
`from llm_tutor.bot.survey import SurveyFlow`):

```python
def test_pending_survey_topic(conn, settings) -> None:
    load_two_modules(conn)
    assert start.pending_survey_topic(conn).number == 1  # новый ученик

    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)
    enter_module(conn, 2, completed=(1,))
    assert start.pending_survey_topic(conn).number == 2

    survey.apply_answers(conn, T2, {"t02_basics": 1, "t02_relations": 1}, settings=settings)
    assert start.pending_survey_topic(conn) is None


async def test_finishing_module_one_opens_module_two_survey(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)
    router = make_router(conn, GradingTutor(conn, passed=True), "m", settings=settings)
    state = _fsm()
    message = FakeMessage(correct_answer(item))

    await _named(router, "message", "on_text")(message, state)

    assert any("🎉 Модуль 1" in text for text, _ in message.sent)
    assert message.sent[-1][0].startswith("📘 Модуль 2")
    assert await state.get_state() == SurveyFlow.question.state
    assert (await state.get_data())["topic"] == 2


async def test_module_two_survey_keeps_module_one_and_starts_lesson(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)
    enter_module(conn, 2, completed=(1,))
    session_id = repos.get_open_session(conn)
    lesson = repos.get_session_state(conn, session_id)
    closed_before = sum(1 for s in lesson.route.steps if s.status == "closed")
    survey_router = make_survey_router(conn, settings, GradingTutor(conn, passed=True), "m")
    state = _fsm()
    intro = await start_survey(FakeMessage(), state, repos.get_topic(conn, 2))
    click = _named(survey_router, "callback_query", "on_survey_click")

    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, T2.level_options[survey.LEVEL_SOME])
    await _press(click, intro, state, survey.SELF_LEVELS[1])
    await _press(click, intro, state, survey.SELF_LEVELS[3])

    after = repos.get_session_state(conn, session_id)
    statuses = {s.concept_id: s.status for s in after.route.steps}
    assert statuses["mini_box"] == statuses["mini_corr"] == "claimed"
    assert sum(1 for s in after.route.steps if s.status == "closed") == closed_before
    assert after.current_node_id == "mini_plots"


async def test_all_confident_module_starts_with_check(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)
    enter_module(conn, 2, completed=(1,))
    session_id = repos.get_open_session(conn)
    router = make_survey_router(conn, settings, GradingTutor(conn, passed=True), "m")
    state = _fsm()
    intro = await start_survey(FakeMessage(), state, repos.get_topic(conn, 2))
    click = _named(router, "callback_query", "on_survey_click")

    await _press(click, intro, state, survey_bot.GO_LABEL)
    await _press(click, intro, state, T2.level_options[survey.LEVEL_CONFIDENT])
    await _press(click, intro, state, survey.SELF_LEVELS[3])

    assert repos.get_session_state(conn, session_id).mode == "verify"


async def test_survey_click_without_module_in_fsm_is_module_one(conn, settings) -> None:
    """Анкета, начатая до обновления: в FSM нет ``topic`` — это модуль 1."""
    load_seed(conn)
    intro, state, click = await _begin(conn, settings)
    data = await state.get_data()
    await state.set_data({k: v for k, v in data.items() if k not in ("topic", "then")})

    await _press(click, intro, state, survey_bot.GO_LABEL)

    assert T1.level_question in intro.edits[-1][0]
```

`_begin` в `test_bot_flows.py` остаётся (`start_survey(FakeMessage(), state)` —
модуль 1 по умолчанию).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bot_flows.py tests/test_modules_turn.py -q`
Expected: FAIL — нет `pending_survey_topic`, `NEXT_TOPIC_SURVEY_NOTE`.

- [ ] **Step 3: Implement**

`core/turn.py`:

```python
# Следующий модуль ещё без анкеты: урок продолжится после неё (§5.2 спеки модулей).
NEXT_TOPIC_SURVEY_NOTE = (
    "Дальше — модуль {number} «{title}». Сначала пара вопросов — подберу, с чего начать."
)
```

В `_close_node_if_ready` после `next_topic = …; route = route.model_copy(…topic_id…)`
и **до** `next_node_id = …`:

```python
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
                        "route": route,
                    }
                ),
                "\n\n".join(notes),
            )
```

(импорт `from llm_tutor.student import survey` в `turn.py` — `core → student` разрешён).

`reset_lesson`:

```python
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
            }
        ),
    )
```

(импорт `from collections.abc import Collection, Mapping`).

`bot/start.py`:

```python
# Вход в модуль 2–10: что за модуль и что сейчас будет (сырой текст).
MODULE_INTRO_TEMPLATE = (
    "📘 Модуль {number} · {title}\n"
    "{intro}\n"
    "Пара коротких вопросов — и подберу, с чего начать."
)


def module_intro_text(topic: Topic) -> str:
    """Приветствие анкеты модуля (сырой текст)."""
    return MODULE_INTRO_TEMPLATE.format(
        number=topic.number, title=topic.title, intro=topic.intro
    )


def pending_survey_topic(conn: sqlite3.Connection) -> Topic | None:
    """Модуль занятия, анкета которого ещё не пройдена (§5.2), иначе ``None``.

    Это чтение, а не ход: сессию не заводим.
    """
    graph = CourseGraph.load(conn)
    if not graph.node_ids:
        return None
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id) if session_id else SessionState()
    topic_id = route_mod.topic_for(graph, state)
    topic = repos.get_topic(conn, topic_id) if topic_id is not None else None
    if topic is None or survey.is_completed(conn, topic.survey):
        return None
    return topic
```

(импорты `Topic, SessionState` из `llm_tutor.schemas`, `from llm_tutor.student import survey`).

`begin_lesson` — новая сигнатура и начало:

```python
async def begin_lesson(
    message: Message,
    state: FSMContext,
    conn: sqlite3.Connection,
    client: LLMClient,
    model: str,
    settings: Settings,
    *,
    claimed: Collection[str] = (),
    start_node_id: str | None = None,
) -> None:
    """Список ближайших шагов и первый ход урока.

    ``claimed`` — темы, которые анкета модуля только что назвала знакомыми.
    ``start_node_id`` — тема, выбранная в меню до анкеты (прыжок в модуль).
    """
    await state.clear()
    reset_lesson(conn, claimed=claimed)
    graph = CourseGraph.load(conn)
    session_id = repos.get_open_session(conn)
    previous = repos.get_session_state(conn, session_id).route if session_id else None
    route = route_mod.build_route(
        conn,
        graph,
        goal_concept_id=route_mod.goal_for(conn, graph),
        previous=previous,
        settings=settings,
    )
    # Первый шаг списка — ровно тот узел, с которого начнётся урок.
    first = (
        start_node_id
        if start_node_id is not None and graph.has_node(start_node_id)
        else route_mod.next_node_id(conn, graph, route, settings=settings)
    )
```

(дальше — `section`/`upcoming` из задачи 9 и прежний код без изменений).

`bot/survey.py`:

```python
# Анкета без модуля — вход в курс: модуль 1 (так же и в FSM до среза 26).
FIRST_TOPIC = 1


def intro_view(topic: Topic | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Приветствие с единственной кнопкой «▶️ Поехали» (готовый HTML).

    Модуль 1 — приветствие курса; дальше — вводная модуля.
    """
    if topic is None or topic.number == FIRST_TOPIC:
        text = start.INTRO_TEXT
    else:
        text = render.escape(start.module_intro_text(topic))
    return text, InlineKeyboardMarkup(inline_keyboard=[[_button(GO_LABEL, GO_DATA)]])


async def start_survey(
    message: Message,
    state: FSMContext,
    topic: Topic | None = None,
    *,
    then_node: str | None = None,
) -> Message:
    """Новое сообщение-приветствие с «Поехали»; анкета модуля привязывается к нему.

    ``then_node`` — тема, к которой ученик шёл из меню: урок начнётся с неё.
    """
    await state.set_state(SurveyFlow.question)
    await state.set_data(
        {
            "message_id": None,
            "given": None,
            "topic": topic.number if topic is not None else FIRST_TOPIC,
            "then": then_node,
        }
    )
    text, markup = intro_view(topic)
    # (дальше — прежний код отправки)
```

В `make_survey_router`:
- `_config(data)` вместо временного `_config()`:

```python
    def _config(data: Mapping) -> SurveyConfig | None:
        """Анкета модуля из FSM; до среза 26 номер не хранился — модуль 1."""
        return survey.config_for(conn, int(data.get("topic") or FIRST_TOPIC))
```

- `on_survey_click`: после проверки `bound` — `config = _config(data)`; если
  `config is None` → `await _orphan_click(callback, state)` и `return`;
  `_progress(data, config)`; на `GO` — `survey.Progress(config)`; финал —
  `await _finish(callback, state, progress, then_node=data.get("then"))`.
- `_finish(callback, state, progress, *, then_node=None)`: после записи ответов

```python
        await start.begin_lesson(
            callback.message,
            state,
            conn,
            client,
            model,
            settings,
            claimed=survey.claimed_concepts(conn, progress.config),
            start_node_id=then_node,
        )
```

- `_orphan_click`: вместо `survey.is_completed(...)`:

```python
        pending = start.pending_survey_topic(conn)
        if pending is None:
            await callback.answer(DONE_TOAST)
            await _drop_keyboard(callback.message)
        elif current == SurveyFlow.question.state:
            ...
        else:
            progress = survey.Progress(pending.survey)
            ...
            await state.set_data(
                {
                    "message_id": callback.message.message_id,
                    "given": _stored(progress),
                    "topic": pending.number,
                    "then": None,
                }
            )
```

`bot/handlers.py` — удалить `_survey_pending` из задачи 11; в `on_start`:

```python
        topic = start.pending_survey_topic(conn)
        if topic is not None:
            await start_survey(message, state, topic)
            return
```

в `on_text` — то же, но не поверх висящего задания (отступление 9):

```python
        # Висит задание (взято командой до анкеты) — текст это ответ на него:
        # анкету предложит `_offer_survey` после хода, ответ не теряется.
        topic = start.pending_survey_topic(conn)
        if topic is not None and _pending_item(conn) is None:
            await start_survey(message, state, topic)
            return
```

Тест в `tests/test_bot_flows.py`:

```python
async def test_answer_to_task_taken_before_module_survey_is_kept(conn, settings) -> None:
    """/task до анкеты модуля 2, потом ответ текстом: ответ засчитан, анкета — следом."""
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)
    enter_module(conn, 2, completed=(1,))
    router = make_router(conn, GradingTutor(conn, passed=True), "m", settings=settings)
    await _named(router, "message", "on_task")(FakeMessage(), _fsm())
    item = repos.get_item(conn, _state(conn).pending_item_id)
    state = _fsm()
    message = FakeMessage(correct_answer(item))

    await _named(router, "message", "on_text")(message, state)

    assert repos.item_was_answered(conn, item.id)
    assert message.sent[-1][0].startswith("📘 Модуль 2")
```

Хелпер внутри `make_router` и вызовы после `_send_reply` в `on_text` и `on_answer`:

```python
    async def _offer_survey(message: Message, state: FSMContext | None) -> None:
        """Ход перевёл в модуль с непройденной анкетой — сразу её приветствие (§5.2)."""
        if state is None:
            return
        topic = start.pending_survey_topic(conn)
        if topic is not None:
            await start_survey(message, state, topic)
```

`on_answer(callback: CallbackQuery, state: FSMContext | None = None)` — после
`await _send_reply(conn, callback.message, reply)`: `await _offer_survey(callback.message, state)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное, включая `test_command_before_survey_does_not_disable_claimed`
(тема, снятая анкетой с «текущей», становится заявленной — отступление 5).

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/bot/start.py src/llm_tutor/bot/survey.py src/llm_tutor/bot/handlers.py src/llm_tutor/core/turn.py tests/test_bot_flows.py tests/test_modules_turn.py
git commit -m "Срез 26: вход в модуль через его анкету, закрытое прошлых модулей не стирается"
```

---

### Task 13: Прыжок в модуль из меню

**Files:**
- Modify: `src/llm_tutor/bot/themes.py` (`on_theme`, `on_theme_go`)
- Test: `tests/test_themes.py`

**Interfaces:**
- Consumes: `start_survey(…, then_node=)`, `survey.is_completed`, `repos.get_topic`.
- Produces: выбор темы модуля с непройденной анкетой запускает анкету этого
  модуля; после неё урок начинается с выбранной темы.

- [ ] **Step 1: Write the failing test** — в `tests/test_themes.py`:

```python
from course_fixtures import T1, load_two_modules
from fakes import _fsm


async def test_jump_into_module_without_survey_starts_its_survey(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, settings=settings)
    repos.ensure_open_session(conn, now=1.0)
    router = themes.make_themes_router(conn, settings)
    state = _fsm()
    message = FakeMessage()

    await _named(router, "callback_query", "on_theme")(
        FakeCallback("theme:mini_plots", message), state
    )

    assert message.sent[-1][0].startswith("📘 Модуль 2")
    data = await state.get_data()
    assert data["topic"] == 2 and data["then"] == "mini_plots"
    assert repos.get_session_state(conn, repos.get_open_session(conn)).current_node_id is None
```

(импорт `from llm_tutor.student import survey`, если его нет).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_themes.py -q`
Expected: FAIL — переход случился сразу, анкеты нет.

- [ ] **Step 3: Implement** — в `make_themes_router`:

```python
    def _survey_first(graph: CourseGraph, node_id: str) -> Topic | None:
        """Модуль темы, анкета которого не пройдена (§5.2), иначе ``None``."""
        topic = repos.get_topic(conn, graph.topic_of(node_id))
        if topic is None or survey.is_completed(conn, topic.survey):
            return None
        return topic

    async def _go(callback: CallbackQuery, state: FSMContext | None, node_id: str) -> None:
        """Переход к теме; в модуль с непройденной анкетой — через анкету."""
        topic = _survey_first(CourseGraph.load(conn), node_id)
        if topic is not None and state is not None:
            await start_survey(callback.message, state, topic, then_node=node_id)
            return
        text = switch_node(conn, node_id, settings=settings)
        await callback.message.answer(text, parse_mode=PARSE_MODE)
```

`on_theme_go(callback, state: FSMContext | None = None)` и
`on_theme(callback, state: FSMContext | None = None)`: вызовы
`text = switch_node(...)` + `answer(...)` заменить на `await _go(callback, state, node_id)`.
Импорты: `FSMContext`, `Topic`, `from llm_tutor.bot.survey import start_survey`,
`from llm_tutor.student import survey`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/bot/themes.py tests/test_themes.py
git commit -m "Срез 26: прыжок в модуль из меню — через анкету модуля"
```

---

### Task 14: Аудит среза 26

- [ ] **Step 1:** Ревью по **Приложению Б**: срез 26, задачи 11–13; фокус —
  Review Focus 2, 3; атомарность `survey:*` (двойной тап на последнем вопросе
  анкеты модуля 2: `asyncio.gather` двух нажатий — урок не стартует дважды);
  старая клавиатура анкеты модуля 1 нажата во время анкеты модуля 2; рестарт
  бота посреди анкеты модуля 2 (FSM потерян → `_orphan_click`).
- [ ] **Step 2:** Триаж, регрессионные тесты, фиксы, `uv run pytest -q`.
- [ ] **Step 3: Commit** `Аудит среза 26: …`.

---

## Срез 27 — меню, промпты, RAG, тексты

### Task 15: Двухуровневое меню «🎚 Темы»

**Files:**
- Modify: `src/llm_tutor/bot/themes.py`
- Modify: `src/llm_tutor/bot/handlers.py` (`on_themes`, меню `themes`)
- Test: `tests/test_themes.py`, `tests/test_bot_flows.py`

**Interfaces:**
- Produces: `themes.topics_keyboard(conn, *, state=None, now=None, settings=None)`
  (кнопка на модуль, `topic:<n>`); `themes.themes_keyboard(conn, topic_id, *,
  state=None, now=None, settings=None)` (темы модуля + «← Модули», `topics`);
  хендлеры `on_topic`, `on_topics`; `MODULE_ICONS`, `TOPICS_DATA = "topics"`.

- [ ] **Step 1: Write the failing tests** — в `tests/test_themes.py`; существующие
  тесты поправить: `themes_keyboard(conn, state=…)` → `themes_keyboard(conn, 1, state=…)`,
  в `test_themes_keyboard_has_button_per_node` считать только `theme:`-кнопки;
  в `test_themes_keyboard_without_session_does_not_open_one` (строка ~303) вызов
  `themes.themes_keyboard(conn, now=0.0, settings=settings)` заменить на
  `themes.themes_keyboard(conn, 1, now=0.0, settings=settings)` и добавить второй вызов
  `themes.topics_keyboard(conn, now=0.0, settings=settings)` (оба — без новой сессии). В
  `tests/test_bot_flows.py::test_themes_command_reports_failure_instead_of_silence`
  патчить `"llm_tutor.bot.handlers.themes.topics_keyboard"`. Новые:

```python
def test_topics_keyboard_has_button_per_module(conn, settings) -> None:
    load_two_modules(conn)

    kb = themes.topics_keyboard(conn, state=SessionState(), now=0.0, settings=settings)

    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == ["topic:1", "topic:2"]
    assert labels[0].startswith("▶️") and labels[1].startswith("🔜")


def test_module_keyboard_has_back_button(conn, settings) -> None:
    load_two_modules(conn)

    kb = themes.themes_keyboard(conn, 2, state=SessionState(), now=0.0, settings=settings)

    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data[-1] == themes.TOPICS_DATA
    assert {d for d in data if d.startswith("theme:")} == {
        "theme:mini_plots", "theme:mini_hist", "theme:mini_box", "theme:mini_corr"
    }


async def test_topic_tap_opens_module_and_back_returns(conn, settings) -> None:
    load_two_modules(conn)
    router = themes.make_themes_router(conn, settings)
    message = FakeMessage()

    await _named(router, "callback_query", "on_topic")(FakeCallback("topic:2", message))
    assert "Модуль 2" in message.edits[-1][0]

    await _named(router, "callback_query", "on_topics")(FakeCallback(themes.TOPICS_DATA, message))
    assert message.edits[-1][0] == themes.THEMES_PROMPT


async def test_unknown_topic_tap_is_answered(conn, settings) -> None:
    load_two_modules(conn)
    router = themes.make_themes_router(conn, settings)
    callback = FakeCallback("topic:abc", FakeMessage())

    await _named(router, "callback_query", "on_topic")(callback)

    assert callback.answered is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_themes.py -q`
Expected: FAIL — нет `topics_keyboard`.

- [ ] **Step 3: Implement** — в `themes.py`:

```python
THEMES_PROMPT = (
    "🎚 <b>Куда идём?</b>\n\n"
    "Выбери модуль — внутри видно, что пройдено и что впереди."
)
MODULE_PROMPT = (
    "Можно вернуться к пройденному или заглянуть вперёд — "
    "про будущее предупрежу, там ещё не закрыты пререквизиты."
)
MODULE_ICONS: dict[str, str] = {"done": "✅", "current": "▶️", "ahead": "🔜"}
TOPICS_DATA = "topics"
TOPICS_BACK_LABEL = "← Модули"


def _read_state(conn: sqlite3.Connection, state: SessionState | None) -> SessionState:
    """Состояние для меню — чтение: сессию НЕ заводим (как в ``_pending_item``)."""
    if state is not None:
        return state
    session_id = repos.get_open_session(conn)
    return repos.get_session_state(conn, session_id) if session_id else SessionState()


def topics_keyboard(
    conn: sqlite3.Connection,
    *,
    state: SessionState | None = None,
    now: float | None = None,
    settings: Settings | None = None,
) -> InlineKeyboardMarkup:
    """Список модулей со статусами: пройден, текущий, впереди."""
    s = settings or get_settings()
    graph = CourseGraph.load(conn)
    session_state = _read_state(conn, state)
    current = route_mod.topic_for(graph, session_state)
    rows = []
    for topic in repos.get_topics(conn):
        nodes = graph.topic_nodes(topic.number)
        if nodes and all(
            _is_closed(conn, node, session_state, now=now, settings=s) for node in nodes
        ):
            status = "done"
        elif topic.number == current:
            status = "current"
        else:
            status = "ahead"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{MODULE_ICONS[status]} {topic.number}. {topic.title}",
                    callback_data=f"topic:{topic.number}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

`themes_keyboard(conn, topic_id, *, state=None, now=None, settings=None)` —
перебор `graph.topic_nodes(topic_id)` вместо `graph.topo_order()`, состояние через
`_read_state`, в конец `rows` добавить
`[InlineKeyboardButton(text=TOPICS_BACK_LABEL, callback_data=TOPICS_DATA)]`.

Хендлеры в `make_themes_router` (импорт `from llm_tutor.bot.survey import safe_edit`):

```python
    @router.callback_query(F.data.startswith("topic:"))
    async def on_topic(callback: CallbackQuery) -> None:
        raw = (callback.data or "").split(":", 1)[1]
        try:
            topic = repos.get_topic(conn, int(raw)) if raw.isdigit() else None
            if topic is None:
                await callback.answer("Модуль недоступен")
                return
            text = f"📘 <b>Модуль {topic.number} · {escape(topic.title)}</b>\n\n{MODULE_PROMPT}"
            await safe_edit(
                callback.message, text, themes_keyboard(conn, topic.number, settings=settings)
            )
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой списка тем модуля: %s", raw)
            await callback.message.answer(fit(escape(BOT_FAILURE_REPLY)), parse_mode=PARSE_MODE)
        await callback.answer()

    @router.callback_query(F.data == TOPICS_DATA)
    async def on_topics(callback: CallbackQuery) -> None:
        try:
            await safe_edit(callback.message, THEMES_PROMPT, topics_keyboard(conn, settings=settings))
        except Exception:  # noqa: BLE001 — нажатие не должно отвечать молчанием
            logger.exception("Сбой списка модулей")
            await callback.message.answer(fit(escape(BOT_FAILURE_REPLY)), parse_mode=PARSE_MODE)
        await callback.answer()
```

`handlers.py`: в `on_themes` и в ветке меню `themes` —
`themes.topics_keyboard(conn, settings=settings)` вместо `themes_keyboard`.
Докстринг модуля `themes.py`: «Навигация по курсу: модули, внутри — темы со статусами».

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/bot/themes.py src/llm_tutor/bot/handlers.py tests/test_themes.py tests/test_bot_flows.py
git commit -m "Срез 27: двухуровневое меню — модули, внутри темы"
```

---

### Task 16: Промпт, контекст и RAG по модулю

**Files:**
- Modify: `src/llm_tutor/llm/prompts.py`
- Modify: `src/llm_tutor/core/context.py`
- Modify: `src/llm_tutor/rag/retriever.py`
- Modify: `src/llm_tutor/course/ingest.py`
- Test: `tests/test_prompts.py`, `tests/test_context.py`, `tests/test_retriever.py`, `tests/test_ingest.py`

**Interfaces:**
- Produces: `prompts.course_line(topic: Topic | None) -> str`;
  `tutor_system_prompt(..., topic: Topic | None = None)`;
  `retrieve(conn, query, *, concept_id=None, k=4, max_topic: int | None = None)`;
  `ingest_text/ingest_url/ingest_file(..., topic_id: int = 1)`; CLI `ingest`
  с обязательным `--topic N` и необязательным `--source-url`.

- [ ] **Step 1: Write the failing tests**

`tests/test_prompts.py`:

```python
from course_fixtures import T2
from llm_tutor.schemas import Topic


def test_prompt_bases_have_no_hardcoded_module() -> None:
    for base in (TUTOR_SYSTEM_PROMPT, *NO_FRAGMENTS_PROMPTS):
        assert "Pandas / EDA" not in base and "groupby" not in base


def test_prompt_names_current_module() -> None:
    topic = Topic(number=2, title="Визуальный анализ", intro="…", survey=T2)

    assert "модуль 2 «Визуальный анализ»" in tutor_system_prompt(0, topic=topic)
    assert "mlcourse.ai" in tutor_system_prompt(0)
```

`tests/test_context.py` (импорты `from course_fixtures import T2, finish_all_but, load_two_modules`):

```python
def test_context_speaks_about_current_module(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T2, {"t02_basics": 1, "t02_relations": 2}, settings=settings)
    finish_all_but(conn, "mini_hist", topic_id=2, completed=(1,))
    session_id = repos.get_open_session(conn)

    package = build_context(conn, session_id, "что такое bins?", graph=CourseGraph.load(conn), now=2.0, settings=settings)

    system = package.messages[0].content
    assert "модуль 2" in system
    assert "Простые графики" in system          # профиль — анкета модуля 2
    assert "Группировки и EDA" not in system    # анкета модуля 1 в профиль не идёт
```

`tests/test_retriever.py`:

```python
def test_retrieve_skips_future_modules(conn) -> None:
    repos.replace_chunks(conn, "u1", [Chunk(source_url="u1", content="gradient boosting basics", seq=0, topic_id=1)])
    repos.replace_chunks(conn, "u3", [Chunk(source_url="u3", content="gradient boosting trees", seq=0, topic_id=3)])

    found = retrieve(conn, "gradient boosting", max_topic=2)

    assert [chunk.source_url for chunk in found] == ["u1"]
    assert found[0].topic_id == 1
```

`tests/test_ingest.py`:

```python
def test_ingest_text_marks_module(conn) -> None:
    ingest_text(conn, "# T\n\n## S\n\nseaborn pairplot\n", "http://u", topic_id=2)

    assert {r[0] for r in conn.execute("SELECT topic_id FROM chunks")} == {2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py tests/test_context.py tests/test_retriever.py tests/test_ingest.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`prompts.py` — из трёх баз убрать первую строку «Ты — тьютор … тема 1 «Pandas / EDA».»
(её заменяет `course_line`), а из правил — примеры, верные только для pandas: в
правиле 3 базы `TUTOR_SYSTEM_PROMPT` пример «(например, «в разделе Grouping»)» →
«по его заголовку», в правиле 5 `TUTOR_NO_MATCH_SYSTEM_PROMPT` список
«(groupby, DataFrame, crosstab, loc/iloc)» → «(названия функций, методов и
терминов)». Остальной текст правил — без изменений. Итог:

```python
TUTOR_SYSTEM_PROMPT = (
    "Правила:\n"
    "1. Отвечай на русском, кратко и по-дружески.\n"
    "2. Опирайся только на фрагменты материалов курса, не выдумывай факты.\n"
    "3. Где уместно — ссылайся на раздел материала по его заголовку.\n"
    "4. Если во фрагментах нет ответа — честно скажи, что этого в материалах не нашёл.\n"
)
TUTOR_NO_MATCH_SYSTEM_PROMPT = (
    "Материалы курса загружены, но по этому запросу ничего не нашлось. Правила:\n"
    f"{_NO_FRAGMENTS_RULES}"
    "5. Если вопрос похож на тему курса, попроси добавить слова из материала "
    "(названия функций, методов и терминов) — поиск идёт по словам материала.\n"
)
TUTOR_NO_MATERIAL_SYSTEM_PROMPT = (
    "Материалов курса сейчас нет. Правила:\n"
    f"{_NO_FRAGMENTS_RULES}"
)


def course_line(topic: Topic | None) -> str:
    """Кто ты и какой модуль идёт — первая строка системного промпта."""
    if topic is None:
        return "Ты — тьютор по курсу машинного обучения mlcourse.ai."
    return (
        "Ты — тьютор по курсу машинного обучения mlcourse.ai, "
        f"модуль {topic.number} «{topic.title}»."
    )
```

`tutor_system_prompt(..., topic: Topic | None = None)`: `parts = [course_line(topic), base, _BREVITY_RULES, …]`.
Импорт `Topic` из `llm_tutor.schemas`.

`retriever.retrieve` — новый параметр `max_topic: int | None = None`; в `SELECT`
добавить `c.topic_id`; фильтр:

```python
    if max_topic is not None:
        # Материалы текущего и прошлых модулей; будущие — нет (§5.5 спеки модулей).
        sql += " AND c.topic_id <= ?"
        params.append(max_topic)
```

и `topic_id=row["topic_id"]` в `Chunk`.

`context.py` — импорт `from llm_tutor.student import route as route_mod`. В
`build_context` перед `retrieve`:

```python
    # Модуль занятия: по нему профиль, маршрут в промпте и материалы (срез 27).
    topic_id = (
        route_mod.topic_for(graph, session_state)
        if graph is not None and graph.node_ids
        else None
    )
    topic = repos.get_topic(conn, topic_id) if topic_id is not None else None
    chunks = retrieve(conn, user_message, k=effective_k, max_topic=topic_id)
```

и `_system_prompt(..., topic=topic)`. В `_system_prompt(conn, state, graph, *,
topic: Topic | None, material, now, settings)`:

```python
    profile = format_profile_block(
        survey.self_assessment(conn, topic.survey) if topic is not None else {}
    )
    ...
    if state.route is not None and graph is not None:
        route_block = format_route_block(
            route_mod.section_route(graph, state.route),
            names={node_id: graph.concept(node_id).name for node_id in graph.node_ids},
        )
    ...
        tutor_system_prompt(
            state.hint_level, material=material, phase=state.phase,
            route_block=route_block, topic=topic,
        ),
```

(временное `config_for(conn, 1)` из задачи 11 удалить).

`ingest.py` — `ingest_text(conn, text, source_url, *, topic_id: int = 1, max_chars=…)`:

```python
    chunks = [
        chunk.model_copy(update={"topic_id": topic_id})
        for chunk in chunk_markdown(to_markdown(text), source_url, max_chars=max_chars)
    ]
    return replace_chunks(conn, source_url, chunks)
```

`ingest_url`/`ingest_file` — тот же `topic_id`, пробрасывается в `ingest_text`.
CLI:

```python
    parser.add_argument("--topic", type=int, required=True, help="номер модуля материала (1–10)")
    parser.add_argument("--source-url", default=None, help="URL страницы для цитат (для файла)")
    ...
        if args.source.startswith(("http://", "https://")):
            count = ingest_url(conn, args.source, topic_id=args.topic)
        else:
            count = ingest_file(conn, args.source, source_url=args.source_url, topic_id=args.topic)
```

Докстринг модуля: строка CLI — `python -m llm_tutor.course.ingest <url|file> --topic N [--source-url URL] [--db PATH]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/llm/prompts.py src/llm_tutor/core/context.py src/llm_tutor/rag/retriever.py src/llm_tutor/course/ingest.py tests/test_prompts.py tests/test_context.py tests/test_retriever.py tests/test_ingest.py
git commit -m "Срез 27: промпт, профиль и материалы — по модулю занятия"
```

---

### Task 17: Тексты входа и сквозной сценарий «модуль 1 → модуль 2»

**Files:**
- Modify: `src/llm_tutor/bot/start.py` (`INTRO_TEXT`, `WELCOME_BACK_TEMPLATE`, `welcome_back_text`)
- Create: `tests/test_e2e_modules.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Produces: приветствие курса про 10 модулей; «С возвращением» называет модуль и тему.

- [ ] **Step 1: Write the failing tests**

`tests/test_handlers.py`:

```python
async def test_welcome_back_names_module_and_topic(conn, settings) -> None:
    load_seed(conn)
    session_id = repos.ensure_open_session(conn, now=1.0)
    repos.update_session_state(conn, session_id, SessionState(current_node_id="groupby"))

    text = start.welcome_back_text(conn)

    name = CourseGraph.load(conn).concept("groupby").name
    assert "Модуль 1" in text and f"«{name}»" in text
```

(импорты в `tests/test_handlers.py`, если их нет: `from llm_tutor.course.graph import CourseGraph`,
`from llm_tutor.schemas import SessionState`).

`tests/test_e2e_modules.py`:

```python
"""Сквозной сценарий: модуль 1 пройден → анкета модуля 2 → урок модуля 2 (срез 27)."""

from course_fixtures import T1, T2, correct_answer, finish_all_but, load_two_modules
from fakes import FakeCallback, FakeMessage, GradingTutor, _fsm, _named
from llm_tutor.bot.handlers import make_router
from llm_tutor.bot.survey import GO_LABEL, make_survey_router
from llm_tutor.db import repos
from llm_tutor.student import survey


def _data_of(message: FakeMessage, label: str) -> str:
    for row in message.reply_markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return button.callback_data
    raise AssertionError(f"нет кнопки {label!r}")


async def test_student_moves_from_module_one_to_module_two(conn, settings) -> None:
    load_two_modules(conn)
    survey.apply_answers(conn, T1, {b.key: 1 for b in T1.blocks}, level=survey.LEVEL_SOME, settings=settings)
    item = finish_all_but(conn, "churn_eda_case", topic_id=1)
    client = GradingTutor(conn, passed=True)
    router = make_router(conn, client, "m", settings=settings)
    survey_router = make_survey_router(conn, settings, client, "m")
    click = _named(survey_router, "callback_query", "on_survey_click")
    state = _fsm()
    chat = FakeMessage(correct_answer(item))

    await _named(router, "message", "on_text")(chat, state)

    texts = [text for text, _ in chat.sent]
    assert any("🎉 Модуль 1" in text for text in texts)
    intro = FakeMessage(texts[-1])
    intro.message_id = (await state.get_data())["message_id"]
    intro.reply_markup = chat.sent[-1][1]

    for label in (GO_LABEL, T2.level_options[survey.LEVEL_SOME], survey.SELF_LEVELS[1], survey.SELF_LEVELS[1]):
        await click(FakeCallback(_data_of(intro, label), intro), state)

    lesson = repos.get_session_state(conn, repos.get_open_session(conn))
    assert lesson.current_node_id == "mini_plots"
    assert lesson.pending_item_id is not None and lesson.pending_item_id >= 2000
    assert any(text.startswith("📋") for text, _ in intro.sent)
    assert survey.is_completed(conn, T2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_handlers.py tests/test_e2e_modules.py -q`
Expected: FAIL — «С возвращением» без модуля (e2e может пройти уже сейчас — тогда
он фиксирует поведение; это допустимо, отметить в журнале).

- [ ] **Step 3: Implement** — `bot/start.py`:

```python
INTRO_TEXT = (
    "👋 Привет! Я тьютор по курсу mlcourse.ai: 10 модулей, от pandas до градиентного бустинга.\n"
    "Пара коротких вопросов — и подберу, с чего начать."
)

WELCOME_BACK_TEMPLATE = "👋 С возвращением! Модуль {number} «{topic}» — продолжаем «{name}»."
```

`welcome_back_text`: после `graph = CourseGraph.load(conn)` и проверки узла:

```python
    topic = repos.get_topic(conn, graph.topic_of(node_id))
    name = graph.concept(node_id).name
    if topic is None:
        return f"👋 С возвращением! Продолжаем «{name}»."
    return WELCOME_BACK_TEMPLATE.format(number=topic.number, topic=topic.title, name=name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: всё зелёное (`test_bot_flows` проверяет, что в `INTRO_TEXT` две
строки и нет `START_LABEL` — новый текст этому отвечает).

- [ ] **Step 5: Commit**

```bash
git add src/llm_tutor/bot/start.py tests/test_handlers.py tests/test_e2e_modules.py
git commit -m "Срез 27: приветствие курса из модулей, сквозной сценарий модуль 1 → 2"
```

---

### Task 18: Аудит среза 27

- [ ] **Step 1:** Ревью по **Приложению Б**: срез 27, задачи 15–17; фокус —
  подделанные колбэки `topic:`/`topics`/`theme:` (мусор, несуществующий номер,
  огромное число), правка сообщения меню, которое уже нельзя править
  (`safe_edit` шлёт новое), HTML-спецсимволы в названии модуля, промпт и RAG
  ученика без маршрута, `ingest` без `--topic`.
- [ ] **Step 2:** Триаж, регрессионные тесты, фиксы, `uv run pytest -q`.
- [ ] **Step 3: Commit** `Аудит среза 27: …`.

---

## Срезы 28–36 — контент модулей 2–10

Каждый модуль — отдельная задача, строго по порядку: межмодульные рёбра и веса
модуля N ссылаются только на модули 1…N−1. Правила контента — §6.2 спеки,
приёмка — §6.3. Материалы скачиваются из репозитория курса
(`https://raw.githubusercontent.com/Yorko/mlcourse.ai/main/mlcourse_ai_jupyter_book/book/`),
страницы книги для цитат — `https://mlcourse.ai/book/`. **Перед первым
скачиванием** показать пользователю список файлов (источник, размер — в
таблицах ниже) и получить «да».

Временная БД для проверок: `<scratch>/check.sqlite3` (scratch-каталог сессии).

### Task 19: Модуль 2 «Визуальный анализ данных» (срез 28)

**Files:** Create `data/seed_topic02.json`; материалы (не коммитятся) `data/raw/topic02_*.md`.

| Файл (`book/topic02/…`) | Размер | В `data/raw/` |
|---|---|---|
| `topic02_intro.md` | 1.7 КБ | `topic02_intro.md` |
| `topic02_visual_data_analysis.md` | 33 КБ | `topic02_visual_data_analysis.md` |
| `topic02_additional_seaborn_matplotlib_plotly.md` | 16 КБ | `topic02_additional_seaborn_matplotlib_plotly.md` |

Опорные понятия (ориентир, финальный список — по материалу): одномерная
визуализация (гистограмма, плотность/KDE, box plot, violin plot, bar/count plot),
многомерная (scatter, scatter matrix/pairplot, heatmap корреляций, crosstab,
категориальный × количественный), API seaborn (jointplot, catplot), plotly
(интерактивные графики), t-SNE. Кандидаты в межмодульные пререквизиты:
`pandas_dataframe`, `describe_stats`, `value_counts`, `groupby`,
`visualization_basics` (модуль 1).

- [ ] **Step 1: Материалы.** Для каждого файла таблицы:
  `curl -sL --max-time 60 <база>/topic02/<файл> -o data/raw/<файл>` и проверить, что
  размер совпадает с таблицей (±10 %).
- [ ] **Step 2: Прочитать материалы** целиком и записать в журнал черновой список
  тем (15–25) с id, рёбрами и обоснованием межмодульных рёбер.
- [ ] **Step 3: Написать `data/seed_topic02.json`** по образцу `seed_topic01.json`:
  `"course": "mlcourse.ai/topic02"`, раздел `topic` (`number: 2`, `title`
  «Визуальный анализ данных», `intro` — 1–2 предложения, `survey` — `level_key`
  `t02_level`, 3 варианта уровня, 3–6 блоков `t02_…`, накрывающих все темы ровно
  один раз, `assumed_by_confident` и `foundation`), `nodes` (с `source_url` на
  страницу книги, у тем-основ без опоры — `null`), `edges` (межмодульные — мягкие,
  вес 0.5), `items` (id с 2001; ≥ 2 заданий с автопроверкой на тему, в основном
  `choice` 3–5 вариантов и `short`; 1–2 `open` с рубрикой), `rubrics`/`criteria`
  (id с 201). Задания, опирающиеся на модуль 1, получают его тему в
  `concept_weights` с весом 0.3–0.5.
- [ ] **Step 4: Проверка кодом.**
  Run: `uv run python -m llm_tutor.course.seed --db <scratch>/check.sqlite3`
  Expected: `Загружено: 2 модулей, …`; каждое «Предупреждение» (жёсткое
  межмодульное ребро) либо убрано, либо обосновано в журнале.
  Run: `uv run pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 2: граф, банк заданий и анкета визуального анализа`
  (в теле — обоснование жёстких межмодульных рёбер, если есть).
- [ ] **Step 6: Адверсариальное ревью контента** агентом `general-purpose` по
  **Приложению А** (модуль 2, файлы `data/seed_topic02.json` и `data/raw/topic02_*`).
- [ ] **Step 7: Триаж и фиксы** (каждая находка — `Ruling:` в журнале), повторить
  Step 4, коммит `Аудит модуля 2: …`.
- [ ] **Step 8: Материалы в RAG (временная БД)** — для каждого файла:
  `uv run python -m llm_tutor.course.ingest data/raw/<файл> --topic 2 --source-url https://mlcourse.ai/book/topic02/<файл без .md>.html --db <scratch>/check.sqlite3`
  → «Загружено чанков: N», N > 0.
- [ ] **Step 9:** `uv run pytest -q` → всё зелёное.

### Task 20: Модуль 3 «Классификация, деревья решений и kNN» (срез 29)

**Files:** Create `data/seed_topic03.json`; `data/raw/topic03_*.md`.

| Файл (`book/topic03/…`) | Размер |
|---|---|
| `topic03_intro.md` | 1.9 КБ |
| `topic03_decision_trees_kNN.md` | 56 КБ |

Опорные понятия: постановка задачи классификации; дерево решений; энтропия,
прирост информации, критерий Джини; жадное построение дерева; разбиение по
количественному признаку; гиперпараметры (`max_depth`, `min_samples_leaf`,
`max_features`) и переобучение; дерево для регрессии; метод ближайших соседей,
метрики расстояния, выбор k; отложенная выборка и кросс-валидация,
`GridSearchCV`; плюсы и минусы деревьев и kNN. Кандидаты в пререквизиты:
`pandas_dataframe`, `numpy_basics` (модуль 1); визуализация (модуль 2) — мягко.

- [ ] **Step 1: Материалы.** `curl -sL --max-time 60 <база>/topic03/<файл> -o data/raw/<файл>`
  для каждого файла таблицы, размеры сверить.
- [ ] **Step 2:** Прочитать материалы, черновой список тем в журнал.
- [ ] **Step 3:** Написать `data/seed_topic03.json`: `course` `mlcourse.ai/topic03`,
  `topic.number` 3, ключи анкеты `t03_…`, задания с 3001, рубрики и критерии с
  301, межмодульные рёбра мягкие, задания на модули 1–2 — вторичным весом.
- [ ] **Step 4:** `uv run python -m llm_tutor.course.seed --db <scratch>/check.sqlite3`
  → «3 модулей», предупреждения разобраны; `uv run pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 3: граф, банк заданий и анкета деревьев и kNN`.
- [ ] **Step 6:** Ревью контента по **Приложению А** (модуль 3). Эталоны про
  энтропию, Джини и поведение `DecisionTreeClassifier`/`KNeighborsClassifier`
  проверяются запуском (`uv run --with scikit-learn,pandas python <scratch>/check.py`).
- [ ] **Step 7:** Триаж, фиксы, Step 4 повторно, коммит `Аудит модуля 3: …`.
- [ ] **Step 8:** `ingest` каждого файла с `--topic 3`,
  `--source-url https://mlcourse.ai/book/topic03/<имя>.html`, `--db <scratch>/check.sqlite3`.
- [ ] **Step 9:** `uv run pytest -q`.

### Task 21: Модуль 4 «Линейные модели классификации и регрессии» (срез 30)

**Files:** Create `data/seed_topic04.json`; `data/raw/topic04_*.md`.

| Файл (`book/topic04/…`) | Размер |
|---|---|
| `topic04_intro.md` | 3.4 КБ |
| `topic4_linear_models_part1_mse_likelihood_bias_variance.md` | 24 КБ |
| `topic4_linear_models_part2_logit_likelihood_learning.md` | 14 КБ |
| `topic4_linear_models_part3_regul_example.md` | 13 КБ |
| `topic4_linear_models_part4_good_bad_logit_movie_reviews_XOR.md` | 14 КБ |
| `topic4_linear_models_part5_valid_learning_curves.md` | 11 КБ |

Опорные понятия: линейная регрессия, МНК и MSE, метод максимального
правдоподобия, разложение ошибки на смещение и разброс, теорема Гаусса–Маркова;
регуляризация (ridge, lasso); логистическая регрессия, сигмоида, логит, log-loss,
обучение через правдоподобие; регуляризация в логите (параметр `C`); мешок слов
на отзывах о фильмах; XOR и полиномиальные признаки; кривые валидации и
обучения. Кандидаты в пререквизиты: `numpy_basics` (1), темы классификации и
кросс-валидации (3).

- [ ] **Step 1: Материалы** (`curl … <база>/topic04/<файл> -o data/raw/<файл>`, размеры сверить).
- [ ] **Step 2:** Прочитать, черновой список тем в журнал.
- [ ] **Step 3:** `data/seed_topic04.json`: `topic.number` 4, ключи `t04_…`, задания
  с 4001, рубрики и критерии с 401.
- [ ] **Step 4:** `seed --db <scratch>/check.sqlite3` → «4 модулей»;
  `pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 4: граф, банк заданий и анкета линейных моделей`.
- [ ] **Step 6:** Ревью по **Приложению А** (модуль 4); формулы (MSE, log-loss,
  сигмоида) и поведение `LogisticRegression(C=…)` проверяются запуском.
- [ ] **Step 7:** Триаж, фиксы, Step 4, коммит `Аудит модуля 4: …`.
- [ ] **Step 8:** `ingest` каждого файла с `--topic 4` и `--source-url https://mlcourse.ai/book/topic04/<имя>.html`.
- [ ] **Step 9:** `uv run pytest -q`.

### Task 22: Модуль 5 «Бэггинг и случайный лес» (срез 31)

**Files:** Create `data/seed_topic05.json`; `data/raw/topic05_*.md`.

| Файл (`book/topic05/…`) | Размер |
|---|---|
| `topic05_intro.md` | 2.3 КБ |
| `topic5_part1_bagging.md` | 18 КБ |
| `topic5_part2_random_forest.md` | 35 КБ |
| `topic5_part3_feature_importance.md` | 18 КБ |

Опорные понятия: ансамбли, бутстрэп, бэггинг, out-of-bag оценка, снижение
разброса; случайный лес, случайные подпространства признаков, Extra Trees;
гиперпараметры леса; лес для регрессии и классификации; важность признаков
(по уменьшению неоднородности, перестановочная). **Жёсткий** межмодульный
пререквизит ожидаем: дерево решений (модуль 3) → случайный лес — обосновать в
коммите. Остальные — мягкие: смещение/разброс (4), кросс-валидация (3).

- [ ] **Step 1: Материалы** (`curl … <база>/topic05/<файл>`, размеры сверить).
- [ ] **Step 2:** Прочитать, черновой список тем в журнал.
- [ ] **Step 3:** `data/seed_topic05.json`: `topic.number` 5, ключи `t05_…`, задания
  с 5001, рубрики и критерии с 501.
- [ ] **Step 4:** `seed --db <scratch>/check.sqlite3` → «5 модулей»;
  `pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 5: граф, банк заданий и анкета бэггинга и леса`.
- [ ] **Step 6:** Ревью по **Приложению А** (модуль 5); доля объектов вне
  бутстрэп-выборки (≈ 36.8 %) и поведение `RandomForestClassifier(oob_score=True)`
  проверяются запуском.
- [ ] **Step 7:** Триаж, фиксы, Step 4, коммит `Аудит модуля 5: …`.
- [ ] **Step 8:** `ingest` с `--topic 5`, `--source-url https://mlcourse.ai/book/topic05/<имя>.html`.
- [ ] **Step 9:** `uv run pytest -q`.

### Task 23: Модуль 6 «Построение и отбор признаков» (срез 32)

**Files:** Create `data/seed_topic06.json`; `data/raw/topic06_*.md`.

| Файл (`book/topic06/…`) | Размер |
|---|---|
| `topic06_intro.md` | 2.3 КБ |
| `topic6_feature_engineering_feature_selection.md` | 40 КБ |

Опорные понятия: извлечение признаков из текста (мешок слов, n-граммы, TF-IDF,
word2vec), изображений, геоданных, даты и времени (циклическое кодирование);
преобразования (стандартизация, MinMax, логарифм), взаимодействия признаков,
заполнение пропусков; кодирование категорий (one-hot, label, hashing trick);
отбор признаков (статистический, по модели, перебором). Кандидаты в
пререквизиты: `dtype_conversion`, `apply_functions` (1); мешок слов (4); важность
признаков (5).

- [ ] **Step 1: Материалы** (`curl … <база>/topic06/<файл>`, размеры сверить).
- [ ] **Step 2:** Прочитать, черновой список тем в журнал.
- [ ] **Step 3:** `data/seed_topic06.json`: `topic.number` 6, ключи `t06_…`, задания
  с 6001, рубрики и критерии с 601.
- [ ] **Step 4:** `seed --db <scratch>/check.sqlite3` → «6 модулей»;
  `pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 6: граф, банк заданий и анкета признаков`.
- [ ] **Step 6:** Ревью по **Приложению А** (модуль 6); поведение `StandardScaler`,
  `TfidfVectorizer`, `OneHotEncoder` в эталонах проверяется запуском.
- [ ] **Step 7:** Триаж, фиксы, Step 4, коммит `Аудит модуля 6: …`.
- [ ] **Step 8:** `ingest` с `--topic 6`, `--source-url https://mlcourse.ai/book/topic06/<имя>.html`.
- [ ] **Step 9:** `uv run pytest -q`.

### Task 24: Модуль 7 «Обучение без учителя: PCA и кластеризация» (срез 33)

**Files:** Create `data/seed_topic07.json`; `data/raw/topic07_*.md`.

| Файл (`book/topic07/…`) | Размер |
|---|---|
| `topic07_intro.md` | 1.7 КБ |
| `topic7_pca_clustering.md` | 30 КБ |

Опорные понятия: обучение без учителя; PCA (дисперсия, собственные векторы,
доля объяснённой дисперсии, выбор числа компонент); k-means (алгоритм, выбор k,
метод «локтя»); affinity propagation; спектральная кластеризация;
агломеративная кластеризация (связи, дендрограмма); метрики кластеризации (ARI,
AMI, гомогенность, полнота, V-мера, силуэт). Кандидаты в пререквизиты:
`numpy_basics` (1), визуализация и t-SNE (2), стандартизация (6).

- [ ] **Step 1: Материалы** (`curl … <база>/topic07/<файл>`, размеры сверить).
- [ ] **Step 2:** Прочитать, черновой список тем в журнал.
- [ ] **Step 3:** `data/seed_topic07.json`: `topic.number` 7, ключи `t07_…`, задания
  с 7001, рубрики и критерии с 701.
- [ ] **Step 4:** `seed --db <scratch>/check.sqlite3` → «7 модулей»;
  `pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 7: граф, банк заданий и анкета PCA и кластеризации`.
- [ ] **Step 6:** Ревью по **Приложению А** (модуль 7); `explained_variance_ratio_`,
  поведение `KMeans` и границы силуэта проверяются запуском.
- [ ] **Step 7:** Триаж, фиксы, Step 4, коммит `Аудит модуля 7: …`.
- [ ] **Step 8:** `ingest` с `--topic 7`, `--source-url https://mlcourse.ai/book/topic07/<имя>.html`.
- [ ] **Step 9:** `uv run pytest -q`.

### Task 25: Модуль 8 «Обучение на гигабайтах: SGD и Vowpal Wabbit» (срез 34)

**Files:** Create `data/seed_topic08.json`; `data/raw/topic08_*.md`.

| Файл (`book/topic08/…`) | Размер |
|---|---|
| `topic08_intro.md` | 1.9 КБ |
| `topic08_sgd_hashing_vowpal_wabbit.md` | 36 КБ |

Опорные понятия: градиентный спуск, стохастический градиентный спуск, онлайн-
обучение; SGD для линейной и логистической регрессии; категориальные признаки
и hashing trick; Vowpal Wabbit (формат входа, функции потерь, проходы, число бит
хеша); обучение вне памяти на больших данных. Кандидаты в пререквизиты:
линейная и логистическая регрессия (4, ожидаемо **жёсткое** — обосновать),
hashing trick (6) — мягко.

- [ ] **Step 1: Материалы** (`curl … <база>/topic08/<файл>`, размеры сверить).
- [ ] **Step 2:** Прочитать, черновой список тем в журнал.
- [ ] **Step 3:** `data/seed_topic08.json`: `topic.number` 8, ключи `t08_…`, задания
  с 8001, рубрики и критерии с 801.
- [ ] **Step 4:** `seed --db <scratch>/check.sqlite3` → «8 модулей»;
  `pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 8: граф, банк заданий и анкета SGD и Vowpal Wabbit`.
- [ ] **Step 6:** Ревью по **Приложению А** (модуль 8); шаг SGD на маленьком примере
  и поведение `SGDClassifier`/`HashingVectorizer` проверяются запуском (сам Vowpal
  Wabbit не ставится — эталоны о его формате сверяются с материалом).
- [ ] **Step 7:** Триаж, фиксы, Step 4, коммит `Аудит модуля 8: …`.
- [ ] **Step 8:** `ingest` с `--topic 8`, `--source-url https://mlcourse.ai/book/topic08/<имя>.html`.
- [ ] **Step 9:** `uv run pytest -q`.

### Task 26: Модуль 9 «Анализ временных рядов» (срез 35)

**Files:** Create `data/seed_topic09.json`; `data/raw/topic09_*.md`.

| Файл (`book/topic09/…`) | Размер |
|---|---|
| `topic09_intro.md` | 1.7 КБ |
| `topic9_part1_time_series_python.md` | 66 КБ |
| `topic9_part2_facebook_prophet.md` | 32 КБ |

Опорные понятия: временной ряд, метрики качества прогноза (MAE, MAPE и др.);
скользящее среднее, взвешенное среднее, экспоненциальное сглаживание, двойное и
тройное (Хольт–Винтерс); кросс-валидация на временных рядах; стационарность,
тест Дики–Фуллера, дифференцирование, преобразование Бокса–Кокса; (S)ARIMA,
ACF/PACF; признаки из ряда (лаги, дата, target encoding) и линейные модели и
бустинг на них; Prophet (тренд, сезонность, праздники, точки излома).
Кандидаты в пререквизиты: pandas (1), линейная регрессия и регуляризация (4),
кросс-валидация (3).

- [ ] **Step 1: Материалы** (`curl … <база>/topic09/<файл>`, размеры сверить).
- [ ] **Step 2:** Прочитать, черновой список тем в журнал.
- [ ] **Step 3:** `data/seed_topic09.json`: `topic.number` 9, ключи `t09_…`, задания
  с 9001, рубрики и критерии с 901.
- [ ] **Step 4:** `seed --db <scratch>/check.sqlite3` → «9 модулей»;
  `pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 9: граф, банк заданий и анкета временных рядов`.
- [ ] **Step 6:** Ревью по **Приложению А** (модуль 9); поведение `rolling`, `ewm`,
  `shift`, `TimeSeriesSplit` проверяется запуском.
- [ ] **Step 7:** Триаж, фиксы, Step 4, коммит `Аудит модуля 9: …`.
- [ ] **Step 8:** `ingest` с `--topic 9`, `--source-url https://mlcourse.ai/book/topic09/<имя>.html`.
- [ ] **Step 9:** `uv run pytest -q`.

### Task 27: Модуль 10 «Градиентный бустинг» (срез 36)

**Files:** Create `data/seed_topic10.json`; `data/raw/topic10_*.md`.

| Файл (`book/topic10/…`) | Размер |
|---|---|
| `topic10_intro.md` | 1.7 КБ |
| `topic10_gradient_boosting.md` | 37 КБ |

Опорные понятия: история бустинга (AdaBoost); алгоритм GBM (функциональный
градиентный спуск, псевдоостатки); функции потерь (L2, L1, Huber, квантильная,
логистическая, экспоненциальная AdaBoost); слабые модели — деревья; темп
обучения и число итераций; регуляризация (подвыборки — стохастический
бустинг); XGBoost, LightGBM, CatBoost — обзор. Ожидаемо **жёсткие**
пререквизиты: дерево решений (3), градиентный спуск (8) — обосновать; мягкие:
ансамбли и бэггинг (5), функции потерь регрессии и log-loss (4).

- [ ] **Step 1: Материалы** (`curl … <база>/topic10/<файл>`, размеры сверить).
- [ ] **Step 2:** Прочитать, черновой список тем в журнал.
- [ ] **Step 3:** `data/seed_topic10.json`: `topic.number` 10, ключи `t10_…`, задания
  с 10001, рубрики и критерии с 1001.
- [ ] **Step 4:** `seed --db <scratch>/check.sqlite3` → «10 модулей»;
  `pytest tests/test_course_data.py -q` → PASS.
- [ ] **Step 5: Commit** `Модуль 10: граф, банк заданий и анкета градиентного бустинга`.
- [ ] **Step 6:** Ревью по **Приложению А** (модуль 10); псевдоостатки для L2 и
  поведение `GradientBoostingRegressor(learning_rate=…)` проверяются запуском.
- [ ] **Step 7:** Триаж, фиксы, Step 4, коммит `Аудит модуля 10: …`.
- [ ] **Step 8:** `ingest` с `--topic 10`, `--source-url https://mlcourse.ai/book/topic10/<имя>.html`.
- [ ] **Step 9:** `uv run pytest -q`.

---

### Task 28: Финал — рабочая БД, ручная проверка, CLAUDE.md

- [ ] **Step 1: Бэкап рабочей БД** через backup API SQLite (простая копия файла
  в режиме WAL может не захватить данные из `-wal`). Инструментом Write
  сохранить в scratch-каталог `backup_db.py`:

```python
"""Бэкап рабочей БД перед миграцией 004 (задача 28)."""

import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
print(f"Бэкап: {target}")
```

  Run: `uv run python <scratch>/backup_db.py data/llm_tutor.sqlite3 data/llm_tutor.sqlite3.backup-$(date +%Y%m%d-%H%M%S)`
  (существующие бэкапы не трогать).
- [ ] **Step 2: Курс в рабочую БД:** `uv run python -m llm_tutor.course.seed` →
  «Загружено: 10 модулей, …» без ошибок.
- [ ] **Step 3: Материалы в рабочую БД:** `ingest` каждого файла `data/raw/topicNN_*`
  (NN = 02…10) с его `--topic` и `--source-url` (команды из задач 19–27 без `--db`).
- [ ] **Step 4: Ручная проверка с пользователем** (бот: `uv run python -m llm_tutor.bot.main`):
  «С возвращением» называет модуль 1; `/plan` — «модуль 1/10», только темы модуля
  1; «🎚 Темы» — десять модулей, внутри модуля 2 — его темы; тап по теме модуля 2 —
  анкета модуля 2 и урок с выбранной темы. Результат — в журнал.
- [ ] **Step 5: `CLAUDE.md`:** первая строка — «Telegram-бот-тьютор по курсу
  mlcourse.ai: 10 модулей, от Pandas/EDA до градиентного бустинга»; в «Где что»
  — `data/seed_topicNN.json` (модули), `course/seed.load_course`, `tests/course_fixtures.py`;
  «Последняя доработка» — срезы 24–36 (`2026-10-08-course-modules`); в «Подводные
  камни» — «модули грузятся только вместе (`load_course`); `load_seed` — один
  модуль как весь курс», «межмодульные рёбра только назад — иначе
  `CourseGraphError`», «генератор черновиков отложен (§9 спеки модулей)».
- [ ] **Step 6:** Финальное ревью всей ветки отдельным агентом по **Приложению Б**
  (диапазон — вся ветка), триаж, фиксы.
- [ ] **Step 7: Commit** `Финальное ревью: курс из десяти модулей` (CLAUDE.md и фиксы).

---

## Приложение А — ТЗ ревьюера контента модуля

Подставить N, пути и диапазон коммитов. Агент — `general-purpose`.

> Ты — адверсариальный ревьюер учебного контента Telegram-бота-тьютора по
> курсу mlcourse.ai (проект `C:\Users\user\Documents\azati_locked_in\llm_tutor`).
> Проверь модуль N: `data/seed_topicNN.json` (граф тем, банк заданий, рубрики,
> анкета) против материалов `data/raw/topicNN_*.md`. Правила контента — §6.2,
> приёмка — §6.3 спеки `docs/superpowers/specs/2026-10-08-course-modules-design.md`.
> Ничего не правь — только отчёт.
>
> Проверь и для каждой находки приложи доказательство:
> 1. **Эталоны.** У каждого `choice` верный вариант (индекс `answer`) действительно
>    верен, остальные однозначно неверны; `short`-эталон однозначен и совпадёт с
>    правильным ответом ученика после нормализации. Если ответ зависит от
>    поведения кода (numpy, pandas, scikit-learn, statsmodels), **запусти код**:
>    скрипт — в scratch-каталоге, запуск
>    `uv run --with numpy,pandas,scikit-learn python <скрипт>` (в зависимости
>    проекта пакеты не добавлять). В отчёт — код и вывод.
> 2. **Рёбра.** Направление пререквизитов (что действительно нужно знать раньше),
>    жёсткость (жёсткое — только если без пререквизита тему не понять), лишние и
>    пропущенные рёбра. Межмодульные — особенно: здесь LLM ошибаются чаще всего.
> 3. **Межмодульные веса.** Задания, которые опираются на темы прошлых модулей,
>    содержат их в `concept_weights` вторичным весом 0.3–0.5.
> 4. **Опора на материал.** Каждая тема и задание опираются на статьи модуля;
>    тема без опоры допустима только как пререквизит-основа с `source_url: null`.
> 5. **Анкета.** Вопросы понятны новичку, блоки осмысленны, `foundation` —
>    действительно фундамент, `assumed_by_confident` — то, что уверенный ученик
>    точно знает.
> 6. **Качество заданий.** Нет вопросов «на угадывание по форме», варианты одной
>    длины и правдоподобны, нет дублей формулировок, сложность (`difficulty`)
>    правдоподобна.
>
> Формат отчёта: список находок с уровнем CRITICAL (неверный эталон, ребро,
> запирающее модуль) / HIGH / MEDIUM / LOW, для каждой — id задания или ребра,
> суть, доказательство (вывод кода или цитата материала), предлагаемая правка.

## Приложение Б — ТЗ ревьюера среза кода

Подставить срез, задачи, диапазон коммитов и фокус. Агент — `general-purpose`
(агента `fork` может не быть).

> Ты — адверсариальный ревьюер среза N проекта
> `C:\Users\user\Documents\azati_locked_in\llm_tutor` (Telegram-бот-тьютор,
> Python 3.12, aiogram 3, SQLite; принципы и подводные камни — `CLAUDE.md`).
> Спека — `docs/superpowers/specs/2026-10-08-course-modules-design.md`, план —
> `docs/superpowers/plans/2026-10-08-course-modules.md` (задачи …). Изменения —
> `git diff <база>..<конец>`. Ничего не правь.
>
> Ищи ошибки, а не стиль: расхождение со спекой и планом; входы, которые ломают
> поведение (фокус: …); гонки (апдейты идут задачами, `handle_as_tasks=True`);
> нарушение слоёв (`core`/`student` не импортируют `bot`) и конвенции вывода
> (сырой текст vs готовый HTML); потеря данных ученика (журнал `events`, факты
> анкеты, снимок маршрута). **Каждую находку воспроизведи запуском кода**:
> скрипт или тест в scratch-каталоге, `uv run python …` / `uv run pytest …`;
> рабочую БД не трогать — только копию во временном каталоге.
>
> Формат отчёта: CRITICAL / HIGH / MEDIUM / LOW, для каждой находки — файл:строка,
> сценарий (вход → ожидаемое → фактическое), команда воспроизведения и её вывод.
