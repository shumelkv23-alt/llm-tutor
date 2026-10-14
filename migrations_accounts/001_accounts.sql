-- Миграция 001 схемы аккаунтов: пользователи, веб-сессии, записи на курсы.
-- Отдельная БД (data/accounts.sqlite3): у каждого ученика своя БД курса,
-- здесь — только то, что общее для всех (спека веба §4).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,   -- в нижнем регистре
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,          -- scrypt$n$r$p$соль$хеш
  created_at    REAL NOT NULL
);

-- В БД лежит sha256 токена, а не сам токен: утечка файла не даёт войти.
CREATE TABLE IF NOT EXISTS web_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollments (
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  course_id   TEXT NOT NULL,
  enrolled_at REAL NOT NULL,
  PRIMARY KEY (user_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(user_id);
