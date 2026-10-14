-- Миграция 003 аккаунтов: админка (срез 63).
-- is_admin — роль админа; выдаёт её только CLI (make-admin), из веба — никак.
-- last_login_at — время последнего входа или регистрации (NULL: ещё не входил).
-- admin_log — кто из админов что сделал над чьим аккаунтом. admin_id и
-- target_id без внешних ключей: журнал переживает аккаунты. Секретов в
-- details нет.

ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN last_login_at REAL;

CREATE TABLE IF NOT EXISTS admin_log (
  id        INTEGER PRIMARY KEY,
  ts        REAL NOT NULL,
  admin_id  INTEGER NOT NULL,
  action    TEXT NOT NULL,          -- reset_password | close_sessions
  target_id INTEGER NOT NULL,
  details   TEXT                    -- JSON без секретов
);

CREATE INDEX IF NOT EXISTS idx_admin_log_target ON admin_log(target_id, ts);
