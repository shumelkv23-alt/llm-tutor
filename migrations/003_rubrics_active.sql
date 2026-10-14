-- Миграция 003: мягкое удаление рубрик и критериев.
-- Как и у концептов/заданий (миграция 002), убранное из seed гасится, а не
-- удаляется: на рубрику ссылаются задания, а журнал событий не переписываем.

ALTER TABLE rubrics ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE criteria ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
