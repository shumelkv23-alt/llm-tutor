-- Миграция 004 аккаунтов: восстановление пароля по ссылке из письма (срез 70).
-- Одноразовый токен: в письме — сам токен, в БД — только его sha256 (как у
-- web_sessions): утечка файла не даёт сбросить чужой пароль.
-- used_at — момент использования (NULL: ещё не использован); одноразовость
-- держит UPDATE ... WHERE used_at IS NULL (accounts.reset_password), а не
-- отдельная проверка: победителя гонки выдаёт rowcount.
-- Внешний ключ на users с ON DELETE CASCADE — в отличие от admin_log, где его
-- нет намеренно: журнал переживает аккаунты, а ссылка без ученика бессмысленна.
-- Срок жизни ссылки — в expires_at; протухшие строки и прежние ссылки ученика
-- гасятся при каждой новой заявке (accounts.create_reset_token), крона нет.

CREATE TABLE IF NOT EXISTS password_resets (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  used_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_password_resets_user    ON password_resets(user_id);
CREATE INDEX IF NOT EXISTS idx_password_resets_expires ON password_resets(expires_at);
