-- Миграция 004: веб-приложение.
-- concepts.theory — конспект узла (Markdown из data/theory/<тема>/<узел>.md):
-- его видит ученик во вкладке «Теория», а тьютор — в промпте.
-- items.starter/setup/tests — для заданий с кодом, который запускается в
-- браузере: заготовка, подготовка данных и проверки assert (срез 46).

ALTER TABLE concepts ADD COLUMN theory TEXT;
ALTER TABLE items ADD COLUMN starter TEXT;
ALTER TABLE items ADD COLUMN setup TEXT;
ALTER TABLE items ADD COLUMN tests TEXT;
