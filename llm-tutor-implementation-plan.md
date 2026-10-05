# План реализации: LLM-тьютор по курсу машинного обучения

> Основан на `llm-tutor-architecture.md`. Курс-пилот: **mlcourse.ai, topic01 «Pandas / Exploratory Data Analysis»**.
> Стратегия сборки: **«тьютор сначала, тонкий сквозной»** — рабочий чат появляется рано, ITS-интеллект добавляется срезами.

---

## 0. Принятые решения

| Вопрос | Решение |
|---|---|
| Интерфейс | Telegram-бот (Python, aiogram 3.x) |
| LLM | OpenRouter API, слой абстракции модели через конфиг (буду пробовать разные модели) |
| Аудитория | Личный эксперимент, 1 пользователь, без auth, один профиль (столбца `user_id` в БД нет) |
| Хранилище | SQLite (raw `sqlite3` + FTS5 для RAG), без ORM/pgvector/Redis/Docker |
| Курс | mlcourse.ai (ODS ML course), пилот topic01 |
| Задачи по коду | Проверка по рубрике БЕЗ исполнения (ученик пишет код текстом) |
| Глубина плана | MVP детально, Фазы 2–3 — дорожной картой |

**Главный принцип:** LLM — интерфейс и генератор, а не источник истины. Состояние ученика, планирование и проверка живут в БД и обычном коде. Источник истины — журнал `events`; `mastery` — производная, пересчитывается кодом.

---

## 1. Технологический стек

| Слой | Выбор | Версия | Почему |
|---|---|---|---|
| Язык | Python | 3.12 | Wheels под всё, минимум головной боли |
| Пакеты/окружение | uv | 0.6.x | Один инструмент вместо venv+pip: `uv sync`, `uv run` |
| Бот | aiogram | 3.x | Async-native, router/dispatcher, встроенный FSM для лестницы подсказок |
| HTTP для OpenRouter | httpx | 0.28.x | Async, типизированный, прямой контроль запроса, без провайдерского SDK |
| Структурный вывод | pydantic + pydantic-settings | 2.x | Валидация JSON-ответов и конфиг из `.env` |
| БД | stdlib `sqlite3` (+FTS5) | встроен | 1 пользователь, 1 файл; raw SQL + тонкий репозиторий = счётчики видны глазами |
| Граф | NetworkX | 3.x | DAG-проверка, топосортировка, predecessors/successors в памяти |
| Парсинг курса | beautifulsoup4 + markdownify | 4.12+ / 1.x | HTML mlcourse.ai → markdown (сохраняет код-блоки и заголовки) |
| Тесты | pytest + pytest-asyncio + respx (+pytest-cov) | 8.x / 1.x / 0.22 | Async-тесты и мок OpenRouter без сети; покрытие ≥80% |

**Сознательно НЕ берём в MVP (YAGNI):** SQLAlchemy/ORM, pgvector, Redis, Docker, LangChain/агентные фреймворки, auth/мультиарендность, песочницу для кода, async-драйвер БД.

---

## 2. Структура репозитория (целевая)

```text
llm_tutor/
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
├── migrations/
│   └── 001_init.sql
├── data/                        # tutor.db, seed, golden set (gitignored)
│   ├── seed_topic01.json        # захардкоженный граф + рубрики topic01
│   └── golden_set_topic01.json  # 20-50 размеченных ответов для eval
├── src/llm_tutor/
│   ├── config.py                # pydantic-settings: модели, ключ, пороги Beta/приоритета
│   ├── schemas.py               # Event, Observation, GradeResult, PlannedNode, Chunk, SessionState
│   ├── bot/
│   │   ├── main.py              # entrypoint, polling, регистрация роутеров
│   │   └── handlers.py          # Telegram-хендлеры → core.turn.handle_turn()
│   ├── db/
│   │   ├── connection.py        # get_conn(), WAL, foreign_keys, миграции
│   │   └── repos.py             # тонкие CRUD
│   ├── llm/
│   │   ├── client.py            # LLMClient: chat()/chat_structured(), абстракция модели
│   │   ├── schemas.py           # pydantic-модели ответов
│   │   └── prompts.py           # шаблоны: тьютор, грейдер, экстрактор
│   ├── course/
│   │   ├── graph.py             # CourseGraph: NetworkX из БД/seed, DAG, обходы
│   │   ├── ingest.py            # fetch+parse+chunk страниц mlcourse.ai → chunks + FTS
│   │   └── seed.py              # загрузка data/seed_topic01.json
│   ├── rag/
│   │   └── retriever.py         # FTS5-поиск, возврат чанков с цитатами
│   ├── student/
│   │   ├── beta.py              # Beta-модель: update/estimate/decay/распространение
│   │   ├── planner.py           # priority(), modes, ready_nodes()
│   │   └── diagnostic.py        # анкета + адаптивная диагностика
│   ├── grader/
│   │   ├── autocheck.py         # детерминированная проверка choice/short (без LLM)
│   │   └── rubric.py            # рубричный грейдер + проверка цитат кодом
│   └── core/
│       ├── context.py           # build_context()
│       └── turn.py              # handle_turn()/post_turn()
└── tests/
    ├── conftest.py
    ├── test_config.py / test_llm_client.py / test_db.py
    ├── test_ingest.py / test_retriever.py / test_graph.py
    ├── test_beta.py / test_planner.py / test_diagnostic.py / test_context.py
    ├── test_turn.py / test_grader.py / test_autocheck.py / test_e2e_topic01.py
    ├── test_smoke.py
```

---

## 3. Схема данных (SQLite)

Single-user: столбца `user_id` нет (добавим при втором профиле). `mastery` — производная, пересчитывается из `events`.

```sql
PRAGMA foreign_keys = ON;   -- PRAGMA user_version = 1

CREATE TABLE concepts (
  id          TEXT PRIMARY KEY,          -- стабильный slug: 'pandas_dataframe'
  name        TEXT NOT NULL,
  difficulty  REAL NOT NULL DEFAULT 0.5, -- 0..1
  description TEXT,
  source_url  TEXT
);

CREATE TABLE edges (
  from_id TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  to_id   TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  type    TEXT NOT NULL DEFAULT 'requires', -- requires | part_of | leads_to
  hard    INTEGER NOT NULL DEFAULT 1,       -- 1 жёсткий, 0 мягкий
  weight  REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (from_id, to_id, type)
);

CREATE TABLE rubrics (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE items (                      -- банк заданий/вопросов
  id              INTEGER PRIMARY KEY,
  concept_weights TEXT NOT NULL DEFAULT '{}', -- JSON {"pandas_dataframe": 1.0}
  difficulty      REAL NOT NULL DEFAULT 0.5,
  answer_type     TEXT NOT NULL DEFAULT 'open', -- open | code | choice | short
  prompt          TEXT NOT NULL,
  options         TEXT NOT NULL DEFAULT '[]',   -- JSON: варианты для choice
  answer          TEXT,                          -- эталон: short/checked; для choice — индекс
  guess           REAL,                          -- для Phase 2 (BKT), в MVP не используется
  slip            REAL,                          -- для Phase 2 (BKT), в MVP не используется
  rubric_id       INTEGER REFERENCES rubrics(id)
);

CREATE TABLE criteria (                   -- атомарные бинарные критерии рубрики
  id               INTEGER PRIMARY KEY,
  rubric_id        INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
  criterion        TEXT NOT NULL,         -- 'содержит .groupby() по колонке'
  weight           REAL NOT NULL DEFAULT 1.0,
  positive_example TEXT,                  -- калибровочный «засчитано»
  negative_example TEXT                   -- калибровочный «не засчитано»
);

CREATE TABLE events (                     -- журнал — источник истины
  id         INTEGER PRIMARY KEY,
  ts         REAL NOT NULL,               -- unix time
  concept_id TEXT REFERENCES concepts(id),
  item_id    INTEGER REFERENCES items(id),
  source     TEXT NOT NULL,               -- autotest | checked | rubric | dialogue | self
  result     REAL NOT NULL,               -- 0..1 (1=верно, 0=неверно)
  weight     REAL NOT NULL DEFAULT 1.0,
  citation   TEXT,                        -- цитата-основание
  confidence REAL,
  hints_used INTEGER NOT NULL DEFAULT 0,
  time_spent REAL
);

CREATE TABLE mastery (                    -- производная, пересчитывается кодом
  concept_id  TEXT PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,
  alpha       REAL NOT NULL DEFAULT 1.0,  -- априор Beta(1,1) = «не знаем»
  beta        REAL NOT NULL DEFAULT 1.0,
  last_seen   REAL,
  next_review REAL
);

CREATE TABLE misconceptions (
  id             INTEGER PRIMARY KEY,
  concept_id     TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  text           TEXT NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE sessions (
  id         INTEGER PRIMARY KEY,
  started_at REAL NOT NULL,
  ended_at   REAL,
  state      TEXT NOT NULL DEFAULT '{}',  -- JSON SessionState
  summary    TEXT
);

CREATE TABLE messages (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  ts         REAL NOT NULL,
  role       TEXT NOT NULL,               -- user | assistant | system
  content    TEXT NOT NULL,
  meta       TEXT                         -- JSON: вердикт грейдера, время шага
);

CREATE TABLE chunks (                     -- чанки материалов для RAG
  id         INTEGER PRIMARY KEY,
  concept_id TEXT REFERENCES concepts(id),
  source_url TEXT NOT NULL,
  section    TEXT,                        -- заголовок раздела (для цитирования)
  seq        INTEGER NOT NULL,
  content    TEXT NOT NULL
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  content, section,
  content='chunks', content_rowid='id',
  tokenize = 'unicode61'
);

CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, content, section) VALUES (new.id, new.content, new.section);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content, section) VALUES ('delete', old.id, old.content, old.section);
END;
CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content, section) VALUES ('delete', old.id, old.content, old.section);
  INSERT INTO chunks_fts(rowid, content, section) VALUES (new.id, new.content, new.section);
END;

CREATE TABLE facts (                      -- профиль/предпочтения
  key        TEXT PRIMARY KEY,            -- 'goal', 'time_budget', 'style'
  value      TEXT NOT NULL,
  source     TEXT,
  confidence REAL
);

CREATE INDEX idx_events_concept_ts ON events(concept_id, ts);
CREATE INDEX idx_messages_session ON messages(session_id, ts);
CREATE INDEX idx_chunks_concept ON chunks(concept_id);
```

`SessionState` (JSON в `sessions.state`): `{current_node_id, mode, hint_level, pending_item_id, attempts, plan_snapshot, last_activity}` — меняется после каждого хода.

Примечания к схеме:
- `chunks_fts` — external content поверх `chunks` (триггеры синхронизируют). Ретривер ищет по FTS и джойнит обратно в `chunks` по `rowid`.
- `chunks.concept_id` при ingest заполняется `NULL` (материал topic01 — одна страница, привязка к концептам позже); backfill после загрузки графа (Срез 4).
- `items.guess`/`items.slip` и `misconceptions` в MVP не используются — нужны с Фазы 2 (BKT, узлы-заблуждения).
- Единого `user_id` нет: таблицы single-user по построению. Связь `item → rubric` односторонняя (`items.rubric_id`), концепт приходит через `items.concept_weights`.

---

## 4. Ключевые модули

### 4.1 LLM-клиент (`llm/client.py`)
- `async chat(messages, *, model=None, temperature=0.4, json_schema=None) -> str | T`
- `async chat_structured(messages, schema, *, model=None) -> T`
- **Абстракция модели:** модель — параметр из конфига (`TUTOR_MODEL`, `GRADER_MODEL`, `EXTRACTOR_MODEL`), смена = правка `.env`. Смену провайдера в MVP не делаем.
- **Structured output:** JSON-mode + pydantic-валидация + один ретрай при провале.
- Дефолт: тьютор `anthropic/claude-sonnet-4.5`; грейдер/экстрактор — отдельные ключи (можно удешевить Haiku без правки кода). Точный id сверить с каталогом OpenRouter на момент установки.

### 4.2 Сборщик контекста (`core/context.py`)
- `async build_context(session_id, user_message, now) -> ContextPackage`
- Состав: системные правила → профиль (`facts`) → `SessionState` → срез Beta → резюме прошлых сессий → чанки (`retriever`) → хвост диалога (6–10 реплик) → сообщение ученика.
- При нехватке токенов первыми режутся диалог и материал.

### 4.3 Обработчик хода (`core/turn.py`)
- `async handle_turn(session_id, user_text) -> str`
- `async post_turn(session_id, *, user_msg, assistant_msg, observations, state_patch) -> None`
- Записывает: messages, events, обновление Beta, `sessions.state`, апсерт заблуждений. Всё атомарно.

### 4.4 RAG-ретривер (`rag/retriever.py`)
- `def retrieve(query, *, concept_id=None, k=4) -> list[Chunk]`
- FTS5 `MATCH`, BM25, экранирование спецсимволов. Векторная БД не нужна для topic01.

### 4.5 Граф курса (`course/graph.py`)
- `CourseGraph`: `load()`, `load_seed()`, `prerequisites(node)`, `dependents(node)`, `topo_order()`, `validate_dag()`.

### 4.6 Рубричный грейдер (`grader/rubric.py`)
- `async grade(item_id, answer) -> GradeResult` (`{criteria: [{criterion, passed, quote}], score, confidence}`) — внутри грузит `items.prompt` + `items.rubric_id` → `criteria`.
- Двухшаговый: (1) изолированный LLM-вызов с критериями + калибровочными примерами → JSON с дословной цитатой; (2) **проверка кодом**, что цитата — подстрока ответа ученика (нормализация регистра/пробелов). Нет цитаты → критерий не засчитан. Низкая температура, без истории чата и без уровня ученика. Применяется только к `answer_type IN ('open','code')`.

### 4.7 Beta-модель ученика (`student/beta.py`)
- `estimate(concept_id) -> {mean, alpha, beta, uncertainty}` (mean = α/(α+β)); **decay применяется лениво при чтении** — по `last_seen` значения стягиваются к априору на лету, отдельный планировщик decay не нужен.
- `update(concept_id, weight, correct, now)` (α += weight·correct, β += weight·(1−correct))
- `decay(concept_id, now)` (стягивание к априору: `x' = 1 + (x−1)·exp(−λ·Δt)`)
- `propagate_success(concept_id, weight)` (успех слегка поднимает прямые пререквизиты малым весом)

### 4.8 Планировщик (`student/planner.py`)
- `def ready_nodes(goal_concept_id=None, limit=3) -> list[PlannedNode]` (`{concept_id, mode, priority}`)
- Фильтр: жёсткие пререквизиты освоены (soft — штраф, не блок).
- `priority = w1*важность + w2*пробел + w3*готовность + w4*срочность_повторения − w5*стоимость − w6*штраф_за_недавний_провал`, всё нормализовано к 0..1.
- Режимы по порогам: skip / verify / compressed / full / reinforce / revisit / review.

### 4.9 Детерминированная проверка (`grader/autocheck.py`)
- `def check(item, answer) -> GradeResult` — для `answer_type IN ('choice','short')`, без LLM.
- `choice`: точное сравнение выбранного индекса/варианта; `short`: нормализация (регистр/пробелы/числовой допуск) + сравнение с `items.answer`. Событие пишется с `source='checked'` — это самый надёжный источник свидетельств в MVP (уровни 1–2 из архитектуры; `autotest` появится с песочницей в Фазе 2).

### 4.10 Диагностика и анкета (`student/diagnostic.py`)
- Анкета → `facts` (цель, время, опыт) + начальный априор по концептам через события `source='self'` с минимальным весом.
- Адаптивная диагностика: на каждом шаге выбираем узел с наибольшей неопределённостью (и важностью для цели) → берём информативный `item` из банка (сложность ≈ текущая оценка; для choice — выше с учётом guess) → `autocheck.check()` → `beta.update()` → распространение на пререквизиты. Остановка: порог неопределённости ключевых узлов или лимит вопросов (стартово 15–30, подбирается на пилоте).

---

## 5. Поток данных на один ход

1. Telegram → `bot/handlers.py` → `core.turn.handle_turn(session_id, text)`.
2. Загрузка `sessions.state` (JSON) и текущей сессии (или создание новой).
3. Ветвление: если стоит `state.pending_item_id` и пришёл ответ — `choice`/`short` → `autocheck.check()` (детерминированно, без LLM), `open`/`code` → `grader.rubric.grade()` (рубрика); иначе → путь тьютора.
4. `core.context.build_context()` → пакет контекста заново из БД.
5. Генерация ответа: тьютор `chat(...)` или грейдер `grade(...)` + отдельный вызов тьютора формулирует ответ по вердикту.
6. (Позже) экстрактор → `Observation` из диалога, слабый вес.
7. `core.turn.post_turn()` → messages, events, `beta.update()`, обновление `sessions.state`, misconceptions.
8. `planner.ready_nodes()` реактивно (при смене ключевых оценок) → `plan_snapshot`.
9. Ответ → Telegram.
10. Триггер конца сессии: LLM-резюме, `ended_at`.

**Порядок важен:** `build_context` читает до записи, `post_turn` пишет после — события атомарны.

**Жизненный цикл сессии (MVP):** создаётся на `/start` (или первом сообщении), `ended_at` проставляется командой `/stop`. Авто-закрытие по неактивности — опционально и не критично в MVP. LLM-резюме в конце сессии (`sessions.summary`) — это Фаза 2, в MVP не делается.

---

## 6. План по фазам

### Фаза 0 — Подготовка

Цель: воспроизводимое окружение, пустой репозиторий, ключ OpenRouter.

| # | Задача | Сложность | Критерий готовности |
|---|---|---|---|
| 0.1 | Python 3.12 + uv | S | `python --version` → 3.12, `uv --version` работает |
| 0.2 | git init + .gitignore | S | `.env`, `.venv/`, `__pycache__/`, `data/*.sqlite3`, `data/*.db`, `*.log` игнорируются |
| 0.3 | uv init + pyproject.toml | M | `uv sync` зелёный, `uv run pytest` запускается (0 тестов — ок); `asyncio_mode = "auto"` |
| 0.4 | .env.example + .env | S | `.env.example` закоммичен, `.env` в gitignore, все ключи с дефолтами |
| 0.5 | Ключ OpenRouter | S | `curl` к `/models` с ключом → 200 |
| 0.6 | Дымовой тест зависимостей | S | импорт aiogram/httpx/pydantic/networkx/bs4/markdownify без ошибок; первый коммит |

**Переменные `.env`:** `OPENROUTER_API_KEY` (обязателен), `OPENROUTER_BASE_URL`, `TUTOR_MODEL`, `GRADER_MODEL`, `EXTRACTOR_MODEL`, `DB_PATH=data/llm_tutor.sqlite3`, пороги Beta/планировщика (эвристики из архитектуры).

**Итог Фазы 0:** `git status` чистый, `uv sync` зелёный, ключ работает, первый коммит.

---

### Фаза MVP — 7 вертикальных срезов (+ диагностика, Срез 4.5)

#### Срез 1 — Скелет бота + LLM
Цель: бот отвечает на `/start` реальным ответом модели, модель меняется через `.env`.

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 1.1 | `config.py` | S | pydantic-settings; при пустом ключе падает с ясной ошибкой. Тест `test_config.py` |
| 1.2 | `llm/schemas.py` | S | модели сообщений, roundtrip в формат OpenRouter. Тест в `test_llm_client.py` |
| 1.3 | `llm/client.py` | M | `chat`/`chat_structured`, JSON-mode, ретраи. Тесты: ok, http-error, retry-on-invalid-json |
| 1.4 | `bot/main.py` + `bot/handlers.py` | M | aiogram, `/start` → LLM → ответ с именем модели. Тест хендлера с замоканным клиентом |

**Итог:** живой бот с LLM.

#### Срез 2 — Хранилище + сессии + сообщения
Цель: полная схема БД, sqlite-обёртка, состояние сессии и история; бот пишет/читает из БД.

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 2.1 | `migrations/001_init.sql` | M | вся DDL + FTS5 (external content + триггеры). Тест: схема в `:memory:` |
| 2.2 | `db/connection.py` | S | `get_conn()`, `migrate()` идемпотентно, WAL, foreign_keys. Тест: идемпотентность |
| 2.3 | `schemas.py` (домен) | M | `Event/Observation/GradeResult/PlannedNode/Chunk/SessionState/Message`. Тест: roundtrip SessionState |
| 2.4 | `db/repos.py` | M | CRUD: session/message/event/mastery/fact. Тест: roundtrip, параметризованные запросы |
| 2.5 | Привязать бота к БД | M | `/start` создаёт/грузит сессию, сообщения пишутся, состояние переживает рестарт |

**Итог:** бот с персистентной памятью диалога.

#### Срез 3 — RAG-индекс
Цель: материалы topic01 распарсены, FTS5 находит релевантный материал, бот отвечает с опорой на курс.

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 3.1 | `course/ingest.py` | L | скачать страницу → bs4 → markdownify → чанки 500–800 токенов → `chunks`+`chunks_fts`. Тест на fixture-HTML |
| 3.2 | `rag/retriever.py` | M | FTS5 MATCH, BM25, экранирование. Тест: релевантность, спецсимволы |
| 3.3 | Минимальный `build_context` | M | правила → чанки → хвост → сообщение. Тест: есть чанк, нет краша на пустой БД |
| 3.4 | Включить RAG в бот | M | базовый системный промпт с цитированием; вне курса — «этого нет в курсе» |

**Итог:** RAG работает сквозняком.

#### Срез 4 — Захардкоженный граф + Beta + ready_nodes
Цель: модель ученика и планировщик на коде, без LLM; граф topic01 из seed.

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 4.1 | `data/seed_topic01.json` + `course/seed.py` | M | 15–25 узлов + рёбра hard/soft + веса. Тест: DAG без циклов, ссылки валидны |
| 4.2 | `course/graph.py` | M | NetworkX: `validate_dag`, prereq/dependents, topo. Тест: цикл падает |
| 4.3 | `student/beta.py` | M | update/estimate/decay/propagate_success. Тест: математика, decay к априору, только пререквизиты |
| 4.4 | `student/planner.py` | L | `ready_nodes()` + приоритет + режимы. Тест: блок жёстким пререквизитом, порядок, маппинг режимов |
| 4.5 | Привязать planner к сессии | M | `/plan` показывает готовые узлы с режимом |

**Итог:** модель ученика и маршрут считаются кодом.

---

#### Срез 4.5 — Анкета + автопроверка + адаптивная диагностика
Цель: заполнить модель ученика объективными данными — без этого планировщик работает на едином априоре Beta(1,1) и маршрут не персонализирован.

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 4.6 | `grader/autocheck.py` | M | детерминированная проверка `choice`/`short` (без LLM). Тест: точное сравнение, допуск чисел, нормализация регистра/пробелов |
| 4.7 | Анкета → `facts` + априор | M | на `/start` короткая анкета (опыт, цель, время) → `facts`; низковесные события `source='self'` на релевантные концепты |
| 4.8 | `student/diagnostic.py` | L | выбор узла с макс. неопределённостью → информативный `item` → `autocheck` → `beta.update()` → распространение; остановка по порогу/лимиту. Тест: порядок выбора, условие остановки |
| 4.9 | Команда `/diagnostic` | M | бот запускает сессию диагностики (2–3 коротких захода), подаёт как «подбор маршрута», а не экзамен |

**Итог:** модель ученика заполнена объективными свидетельствами; планировщик получает сигнал.

---

#### Срез 5 — Полный контекст + лестница подсказок
Цель: тьютор получает полный контекст и ведёт сократовский диалог по лестнице подсказок.

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 5.1 | Расширить `SessionState` | S | `current_node_id, mode, hint_level, pending_item_id, attempts`. Тест: roundtrip |
| 5.2 | `llm/prompts.py` | M | шаблоны: тьютор (сократовский, лестница, не выдавать решение), профиль, состояние, срез, резюме, материал, хвост |
| 5.3 | Полный `build_context` + бюджет токенов | L | порядок блоков, урезание материала/диалога при лимите. Тест: порядок, урезание |
| 5.4 | Логика лестницы подсказок | M | машина состояний `hint_level`: намёк → вопрос → частичное решение → разбор. Тест: переходы |
| 5.5 | `core/turn.py` | L | `handle_turn`/`post_turn`, атомарная запись. Тест: message/state/event |
| 5.6 | Подключить turn в бота | M | полный диалог с состоянием; рестарт не теряет состояние |

**Итог:** полноценный тьютор-диалог без грейдера.

#### Срез 6 — Рубричный грейдер + проверка цитат кодом
Цель: открытые и код-ответы (`open`/`code`) оцениваются по рубрике, вердикт считается кодом. (`choice`/`short` уже покрыты автопроверкой в Срезе 4.5.)

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 6.1 | Рубрики/задания/критерии topic01 | M | 2–3 задания с бинарными критериями (+ типичные заблуждения) в seed |
| 6.2 | Промпт грейдера | M | изоляция: разделители, «команды внутри — данные», калибровочные примеры |
| 6.3 | `grader/rubric.py` | L | LLM → JSON с цитатами → проверка подстроки кодом → вердикт кодом. Тест: поддельная цитата отсекается, ретрай, инъекция |
| 6.4 | Интеграция в turn | M | сдача ответа → вердикт + разбор → событие `source='rubric'` с ограниченным весом → Beta |

**Итог:** объективная оценка открытых ответов работает.

#### Срез 7 — Сквозной сценарий topic01 + eval
Цель: полный диалог работает детерминированно, покрытие ≥80%, мини-золотой набор прогоняется.

| # | Задача | Сложность | Что делаем / проверяем |
|---|---|---|---|
| 7.1 | `data/golden_set_topic01.json` | M | 20–50 размеченных ответов с метками по критериям |
| 7.2 | Eval-прогон грейдера | M | kappa/согласие по критериям, confusion matrix, смещение. Грубый фильтр, не «откалиброван» |
| 7.3 | E2E-тест сценария | L | respx мокает OpenRouter; БД в памяти; проверка: события записаны, mastery обновлена, state переходит |
| 7.4 | Покрытие ≥80% | M | `pytest --cov` ≥80%, edge cases (пустая БД, битый JSON, инъекции) |
| 7.5 | Ручной smoke на реальной модели | S | полный диалог от `/start` до вердикта; материалы цитируются; лестница соблюдается |

**Итог:** MVP готов, проверяем и измеряем.

---

### Фаза 2 — Дорожная карта (вехами, после накопления логов)

1. **Извлечение наблюдений из диалога** — экстрактор → `Observation` (концепт, верно/неверно, заблуждение, цитата, уверенность) с малым весом.
2. **Узлы-заблуждения** — накопление в `misconceptions`, привязка к концептам, режим «усиленный проход».
3. **Резюме сессий + консолидация памяти** — LLM-резюме 100–200 слов, иерархия «лог → резюме сессий → резюме периода → профиль».
4. **Калибровка порогов/весов по логам** — рост золотого набора, подбор весов `priority()`, порогов и decay на симуляции.
5. **Векторный поиск** — пересмотреть решение «SQLite без pgvector»: pgvector/GraphRAG для резюме и связывающих вопросов (только когда FTS5 перестанет справляться).
6. **Ансамбль грейдера + регрессионные прогоны** — 3–5 прогонов с голосованием, promptfoo/DeepEval, отдельный тест-набор против переобучения.
7. **Детектор gaming/wheel-spinning** — сигналы по времени на шаг и подсказкам; сниженный вес, мягкий возврат, смена стратегии при «буксовке».
8. **Дашборд преподавателя** — где застревают, матрица ошибок, дрейф грейдера, выборочная ручная проверка ~5% и пограничных.

### Фаза 3 — Дорожная карта (вехами)

1. **Pre/post-эксперимент** — pre/post-тесты, сравнение адаптивного маршрута с линейным, экзамен без ИИ (Bastani et al.: обычный GPT-тьютор дал −17% на экзамене).
2. **Приватность** — просмотр/исправление/экспорт/удаление, минимизация чувствительных полей (GDPR при ЕС).
3. **Версионирование курса** — стабильные id концептов, миграции без поломки `events`.
4. **Правовая проверка** — EU AI Act: образовательные системы оценки/управления обучением — высокий риск при ЕС; уточнить требования на момент запуска.

---

## 7. Критерии успеха MVP

- [ ] `/start` отвечает, модель меняется через `.env`.
- [ ] Сессия и сообщения переживают перезапуск бота.
- [ ] RAG находит материал topic01 и цитирует его.
- [ ] `ready_nodes()` блокирует узел с незакрытым жёстким пререквизитом.
- [ ] `choice`/`short` проверяются детерминированно (`autocheck`), без LLM.
- [ ] После `/diagnostic` Beta на ключевых узлах отличается от априора Beta(1,1).
- [ ] Beta-счётчики обновляются от событий, decay работает.
- [ ] Лестница подсказок не выдаёт решение на нулевом уровне.
- [ ] Грейдер возвращает вердикт по рубрике, поддельные цитаты отсекаются кодом.
- [ ] E2E-сценарий topic01 зелёный на моках.
- [ ] Покрытие ≥80%.

---

## 8. Риски и митигации (MVP)

| Риск | Митигация |
|---|---|
| Грейдер «придумывает» цитаты | проверка подстроки кодом обязательна (Срез 6) |
| Тьютор выдаёт решение под нажимом | политика в промпте + стресс-сценарий в smoke |
| Качество графа topic01 | 15–25 узлов, ручная проверка, DAG-валидация, «3–5 вопросов на узел» |
| Стоимость вызовов при разработке | respx-моки по умолчанию, реальные вызовы только в smoke/eval |
| FTS5 синхронизация | external content + триггеры + тест соответствия |
| Дрейф версий моделей провайдера | модель из конфига, не захардкожена в коде |
| Модель без диагностики стартует «вслепую» | Срез 4.5: анкета + адаптивная диагностика + автопроверка |
| `chunks.concept_id` ссылается на ещё не загруженные концепты | ingest пишет `NULL`, backfill после seed (Срез 4) |
| Диагностика завышает/занижает владение | сверять предсказания с реальными заданиями через неделю (архитектура, секция 5) |
| Выбор «информативного» вопроса — эвристика | подбирать на пилоте; стартовая сложность ≈ текущая оценка |
