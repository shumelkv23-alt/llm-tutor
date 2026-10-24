-- Миграция 007: повторение по расписанию (срез 79).
-- Строка — закрытое занятие на лестнице повторений (student/review.LADDER_DAYS).
-- mastery.next_review расписанием больше не служит.
CREATE TABLE IF NOT EXISTS review_schedule (
  concept_id TEXT PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,
  step       INTEGER NOT NULL DEFAULT 0,
  due_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS review_schedule_due ON review_schedule(due_at);
