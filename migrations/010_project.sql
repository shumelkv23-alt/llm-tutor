-- Миграция 010: проект по курсу с ИИ-наставником (срез 97).
-- Проект — в БД ученика по курсу. brief — копия описания из project.json на
-- момент выбора: правка файла начатый проект не меняет. Реплики чата с
-- наставником — отдельно от messages урока.

CREATE TABLE IF NOT EXISTS projects (
  id          INTEGER PRIMARY KEY,
  brief_id    TEXT NOT NULL,
  brief       TEXT NOT NULL,                -- JSON ProjectBrief на момент выбора
  template    TEXT NOT NULL,                -- id шаблона этапов
  status      TEXT NOT NULL DEFAULT 'idea', -- idea | build | done | archived
  state       TEXT NOT NULL DEFAULT '{}',   -- JSON ProjectState: карточка, заметки, этап
  started_at  REAL NOT NULL,
  updated_at  REAL NOT NULL,
  done_at     REAL
);

CREATE TABLE IF NOT EXISTS project_turns (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  ts         REAL NOT NULL,
  role       TEXT NOT NULL,                 -- student | mentor
  content    TEXT NOT NULL,
  meta       TEXT                           -- JSON: stage, proposal
);

CREATE INDEX IF NOT EXISTS idx_project_turns ON project_turns(project_id, id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
