-- Заметки v2: комментарий ученика и раздел конспекта, к которому относится
-- фрагмент. Оба поля необязательны: старые заметки остаются цитатами.
ALTER TABLE notes ADD COLUMN comment TEXT;
ALTER TABLE notes ADD COLUMN section_id TEXT;
