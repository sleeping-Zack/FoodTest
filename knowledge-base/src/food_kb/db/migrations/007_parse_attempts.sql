-- Failed pages are retryable work, not immutable successful parse output.
CREATE TABLE parse_attempt (
 attempt_id uuid PRIMARY KEY,
 asset_id text NOT NULL REFERENCES source_asset,
 parser_version text NOT NULL, config_hash text NOT NULL,
 parse_id uuid, retry_of uuid REFERENCES parse_attempt,
 status text NOT NULL CHECK(status IN ('completed','completed_with_issues','duplicate','failed')),
 report_key text NOT NULL UNIQUE CHECK(report_key ~ '^parsing/reports/[0-9a-f-]+\.json$'),
 report_sha256 text NOT NULL CHECK(report_sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(parse_id,asset_id) REFERENCES parse_run(parse_id,asset_id)
);
CREATE INDEX parse_attempt_identity ON parse_attempt(asset_id,parser_version,config_hash);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON parse_attempt FOR EACH ROW EXECUTE FUNCTION immutable_row();
