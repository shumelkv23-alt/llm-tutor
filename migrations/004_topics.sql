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
