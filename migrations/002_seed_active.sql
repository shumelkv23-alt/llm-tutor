-- Миграция 002: мягкое удаление узлов графа и заданий банка.
-- Seed остаётся источником истины для содержимого, но записи, на которые
-- ссылается журнал событий, физически удалять нельзя (журнал не переписываем).
-- Поэтому убранное из seed не удаляется, а гасится флагом active=0.

ALTER TABLE concepts ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE items ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
