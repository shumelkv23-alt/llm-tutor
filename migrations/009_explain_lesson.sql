-- Миграция 009: «Объясни новичку» внутри занятия (срез 92).
-- concepts.explain — эталон занятия (JSON schemas.ExplainConfig): есть — занятие
-- «важное», после его закрытия ученику даётся обязательное объяснение.
-- Сессия получает занятие и тип; старая модульная сессия — concept_id = NULL.

ALTER TABLE concepts ADD COLUMN explain TEXT;                    -- JSON ExplainConfig
ALTER TABLE explain_sessions ADD COLUMN concept_id TEXT;         -- занятие; NULL — старая модульная
ALTER TABLE explain_sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'voluntary';  -- required | voluntary
CREATE INDEX IF NOT EXISTS idx_explain_sessions_concept ON explain_sessions(concept_id, kind, status);
