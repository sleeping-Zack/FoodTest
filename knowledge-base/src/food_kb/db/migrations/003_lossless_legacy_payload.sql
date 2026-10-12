-- JSONB cannot represent U+0000. Raw legacy JSON must remain lossless.
-- Typed knowledge payloads remain JSONB; unrepresentable text is quarantined.
ALTER TABLE legacy_record ALTER COLUMN raw_payload TYPE json USING raw_payload::json;
