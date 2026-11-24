-- Журнал ответов в серии повторения: для выполнения повторений в админке.
-- Строка ложится тем же коммитом, что и ход; due_at — срок, который был у
-- занятия до ответа, поэтому можно отличить ответ в срок от просроченного.
CREATE TABLE IF NOT EXISTS review_log (
    id INTEGER PRIMARY KEY,
    concept_id TEXT REFERENCES concepts(id) ON DELETE SET NULL,
    due_at REAL,
    answered_at REAL NOT NULL,
    passed INTEGER NOT NULL,
    step INTEGER
);

CREATE INDEX IF NOT EXISTS idx_review_log_concept ON review_log(concept_id, answered_at);
