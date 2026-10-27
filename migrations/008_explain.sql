-- Миграция 008: «Объясни новичку» (срез 85).
-- topics.explain — эталон модуля (JSON schemas.ExplainConfig): идеи и загвоздки.
-- Сессия — одна на модуль; реплики сессии лежат отдельно от messages, чтобы
-- новичок никогда не попал в контекст тьютора занятия.

ALTER TABLE topics ADD COLUMN explain TEXT;   -- JSON schemas.ExplainConfig

CREATE TABLE IF NOT EXISTS explain_sessions (
  id         INTEGER PRIMARY KEY,
  topic_id   INTEGER NOT NULL,                -- модуль; ссылки проверяет код (как concepts.topic_id)
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL,                   -- последний ход: по нему «брошенная»
  ended_at   REAL,
  status     TEXT NOT NULL DEFAULT 'active',  -- active | finished
  end_reason TEXT,                            -- button | coverage | limit | stale
  state      TEXT NOT NULL DEFAULT '{}',      -- JSON schemas.ExplainState
  report     TEXT                             -- JSON schemas.ExplainReport
);

CREATE TABLE IF NOT EXISTS explain_turns (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES explain_sessions(id) ON DELETE CASCADE,
  ts         REAL NOT NULL,
  role       TEXT NOT NULL,                   -- student | novice | mentor
  content    TEXT NOT NULL,
  meta       TEXT                             -- JSON: pasted, directive, pitfall_id, lesson, ideas
);

CREATE INDEX IF NOT EXISTS idx_explain_turns_session ON explain_turns(session_id, id);
CREATE INDEX IF NOT EXISTS idx_explain_sessions_status ON explain_sessions(status);
