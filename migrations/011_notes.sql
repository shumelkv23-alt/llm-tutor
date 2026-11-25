-- Миграция 011: личные заметки ученика в конспекте (2026-11-25).
-- Заметка привязана к записи конспекта — реплике-вопросу ученика: отдельного
-- id у записи нет, а messages живут в той же БД. Пустой текст строки не
-- держит (удаляется), правка — upsert по message_id. Каскад уберёт заметку
-- вместе с репликой, если её когда-нибудь удалят.

CREATE TABLE IF NOT EXISTS note_entries (
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  text       TEXT NOT NULL,
  updated_at REAL NOT NULL
);
