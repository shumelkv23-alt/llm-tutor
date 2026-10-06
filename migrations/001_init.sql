-- Миграция 001: базовая схема LLM-тьютора (single-user).
-- Применяется идемпотентно (все объекты IF NOT EXISTS); версию схемы
-- выставляет код после применения (PRAGMA user_version), см. db/connection.py.

PRAGMA foreign_keys = ON;

-- --- Граф концептов курса (Срез 4) ---

CREATE TABLE IF NOT EXISTS concepts (
  id          TEXT PRIMARY KEY,           -- стабильный slug: 'pandas_dataframe'
  name        TEXT NOT NULL,
  difficulty  REAL NOT NULL DEFAULT 0.5,  -- 0..1
  description TEXT,
  source_url  TEXT
);

CREATE TABLE IF NOT EXISTS edges (
  from_id TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  to_id   TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  type    TEXT NOT NULL DEFAULT 'requires',  -- requires | part_of | leads_to
  hard    INTEGER NOT NULL DEFAULT 1,        -- 1 жёсткий, 0 мягкий
  weight  REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (from_id, to_id, type)
);

-- --- Банк заданий и рубрики (Срез 6) ---

CREATE TABLE IF NOT EXISTS rubrics (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id              INTEGER PRIMARY KEY,
  concept_weights TEXT NOT NULL DEFAULT '{}',  -- JSON {"pandas_dataframe": 1.0}
  difficulty      REAL NOT NULL DEFAULT 0.5,
  answer_type     TEXT NOT NULL DEFAULT 'open', -- open | code | choice | short
  prompt          TEXT NOT NULL,
  options         TEXT NOT NULL DEFAULT '[]',   -- JSON: варианты для choice
  answer          TEXT,                          -- эталон: short/checked; для choice — индекс
  guess           REAL,                          -- Phase 2 (BKT), в MVP не используется
  slip            REAL,                          -- Phase 2 (BKT), в MVP не используется
  rubric_id       INTEGER REFERENCES rubrics(id)
);

CREATE TABLE IF NOT EXISTS criteria (            -- атомарные бинарные критерии рубрики
  id               INTEGER PRIMARY KEY,
  rubric_id        INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
  criterion        TEXT NOT NULL,                -- 'содержит .groupby() по колонке'
  weight           REAL NOT NULL DEFAULT 1.0,
  positive_example TEXT,                         -- калибровочный «засчитано»
  negative_example TEXT                          -- калибровочный «не засчитано»
);

-- --- Журнал событий (источник истины) и производная модель ученика ---

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY,
  ts         REAL NOT NULL,                 -- unix time
  concept_id TEXT REFERENCES concepts(id),
  item_id    INTEGER REFERENCES items(id),
  source     TEXT NOT NULL,                 -- autotest | checked | rubric | dialogue | self
  result     REAL NOT NULL,                 -- 0..1 (1=верно, 0=неверно)
  weight     REAL NOT NULL DEFAULT 1.0,
  citation   TEXT,                          -- цитата-основание
  confidence REAL,
  hints_used INTEGER NOT NULL DEFAULT 0,
  time_spent REAL
);

CREATE TABLE IF NOT EXISTS mastery (          -- производная, пересчитывается кодом
  concept_id  TEXT PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,
  alpha       REAL NOT NULL DEFAULT 1.0,      -- априор Beta(1,1) = «не знаем»
  beta        REAL NOT NULL DEFAULT 1.0,
  last_seen   REAL,
  next_review REAL
);

CREATE TABLE IF NOT EXISTS misconceptions (
  id             INTEGER PRIMARY KEY,
  concept_id     TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  text           TEXT NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 1
);

-- --- Сессии и сообщения ---

CREATE TABLE IF NOT EXISTS sessions (
  id         INTEGER PRIMARY KEY,
  started_at REAL NOT NULL,
  ended_at   REAL,
  state      TEXT NOT NULL DEFAULT '{}',     -- JSON SessionState
  summary    TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  ts         REAL NOT NULL,
  role       TEXT NOT NULL,                  -- user | assistant | system
  content    TEXT NOT NULL,
  meta       TEXT                            -- JSON: вердикт грейдера, время шага
);

-- --- Профиль/предпочтения (анкета, Срез 4.5) ---

CREATE TABLE IF NOT EXISTS facts (
  key        TEXT PRIMARY KEY,               -- 'goal', 'time_budget', 'style'
  value      TEXT NOT NULL,
  source     TEXT,
  confidence REAL
);

-- --- Материалы курса для RAG (Срез 3) ---

CREATE TABLE IF NOT EXISTS chunks (
  id         INTEGER PRIMARY KEY,
  concept_id TEXT REFERENCES concepts(id),
  source_url TEXT NOT NULL,
  section    TEXT,                           -- заголовок раздела (для цитирования)
  seq        INTEGER NOT NULL,
  content    TEXT NOT NULL
);

-- FTS5 поверх chunks (external content): триггеры синхронизируют индекс.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  content, section,
  content='chunks', content_rowid='id',
  tokenize = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, content, section) VALUES (new.id, new.content, new.section);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content, section)
    VALUES ('delete', old.id, old.content, old.section);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content, section)
    VALUES ('delete', old.id, old.content, old.section);
  INSERT INTO chunks_fts(rowid, content, section) VALUES (new.id, new.content, new.section);
END;

-- --- Индексы ---

CREATE INDEX IF NOT EXISTS idx_events_concept_ts ON events(concept_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_chunks_concept ON chunks(concept_id);
