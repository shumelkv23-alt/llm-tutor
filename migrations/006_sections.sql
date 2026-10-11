-- Миграция 006: занятия с разделами (срез 53).
-- concepts.sections — разделы занятия: JSON-список {id, title} (schemas.Section);
-- у узла в старом формате (тема без разделов) — пустой список.
-- items.section — раздел главного концепта задания; items.final — итоговое
-- задание занятия (у него раздела нет).
-- topics.published — модуль открыт ученикам; неопубликованный виден как «скоро».

ALTER TABLE concepts ADD COLUMN sections TEXT NOT NULL DEFAULT '[]';
ALTER TABLE items ADD COLUMN section TEXT;
ALTER TABLE items ADD COLUMN final INTEGER NOT NULL DEFAULT 0;
ALTER TABLE topics ADD COLUMN published INTEGER NOT NULL DEFAULT 1;
