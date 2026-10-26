-- Материализованная сводка прогресса: одна JSON-запись на ученика и курс.
-- Админка читает её вместо обхода всех БД учеников; строка обновляется после
-- хода и при записи на курс. user_id ссылается на users: удаление аккаунта
-- уносит и сводку.
CREATE TABLE IF NOT EXISTS course_progress (
    course_id TEXT NOT NULL,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    data      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (course_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_course_progress_course ON course_progress(course_id);
