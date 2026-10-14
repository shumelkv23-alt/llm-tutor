-- Миграция 002 аккаунтов: курс mlcourse-topic01 стал курсом mlcourse из пяти
-- модулей (срез 52). Записи на курс переезжают на новый id; файл БД ученика
-- переименовывает web/userdb.py при первом открытии.
-- UPDATE OR IGNORE: если запись на новый id уже есть, старая просто уходит.

UPDATE OR IGNORE enrollments SET course_id = 'mlcourse' WHERE course_id = 'mlcourse-topic01';
DELETE FROM enrollments WHERE course_id = 'mlcourse-topic01';
