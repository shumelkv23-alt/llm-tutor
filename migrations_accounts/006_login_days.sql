-- Миграция 006 аккаунтов (в ветке sanya была 005; номер 005 занят course_progress ветки admin): история входов по дням для «Пульса ученика».
-- Одна строка — один ученик и один календарный день; при повторном входе
-- растут last_login_at и logins, а first_login_at остаётся первым за день.
-- День хранится строкой YYYY-MM-DD по часам сервера, как и format_time.

CREATE TABLE IF NOT EXISTS login_days (
  user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day            TEXT NOT NULL,
  first_login_at REAL NOT NULL,
  last_login_at  REAL NOT NULL,
  logins         INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, day)
);

CREATE INDEX IF NOT EXISTS idx_login_days_day ON login_days(day);

-- Старым ученикам известен только последний вход: сохраняем хотя бы его,
-- чтобы «Пульс» не показывал ноль дней входа сразу после обновления.
INSERT OR IGNORE INTO login_days (user_id, day, first_login_at, last_login_at, logins)
SELECT id,
       strftime('%Y-%m-%d', last_login_at, 'unixepoch', 'localtime'),
       last_login_at,
       last_login_at,
       1
FROM users
WHERE last_login_at IS NOT NULL
  AND strftime('%Y-%m-%d', last_login_at, 'unixepoch', 'localtime') IS NOT NULL;
