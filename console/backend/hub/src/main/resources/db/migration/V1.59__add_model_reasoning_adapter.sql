-- Immutable model versions opt into provider reasoning support. Existing versions
-- retain their OpenAI-compatible adapter; credentials and endpoint policy are unchanged.
ALTER TABLE oa_model_connection_version
    ADD COLUMN adapter VARCHAR(16) NOT NULL DEFAULT 'openai',
    ADD COLUMN thinking_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD CONSTRAINT ck_model_adapter CHECK (adapter IN ('openai','deepseek')),
    ADD CONSTRAINT ck_model_thinking CHECK (thinking_enabled = FALSE OR adapter = 'deepseek');
