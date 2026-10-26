-- Заметки ученика: сохранённый фрагмент конспекта/задания/кода.
-- Источник и занятие нужны, чтобы показать заметку в профиле и найти её.
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    concept_id TEXT REFERENCES concepts(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    quote TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_concept ON notes(concept_id);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at);
