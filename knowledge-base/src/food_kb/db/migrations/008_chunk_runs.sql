-- Each structural strategy is immutable and can coexist with earlier chunk runs.
CREATE TABLE chunk_run (
 run_id uuid PRIMARY KEY, parse_id uuid NOT NULL REFERENCES parse_run,
 strategy_version text NOT NULL, config_hash text NOT NULL,
 configuration jsonb NOT NULL, output_hash text NOT NULL,
 report_key text NOT NULL, report_sha256 text NOT NULL,
 source_manifest jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(parse_id,strategy_version,config_hash), UNIQUE(run_id,parse_id)
);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON chunk_run FOR EACH ROW EXECUTE FUNCTION immutable_row();
ALTER TABLE chunk ADD COLUMN chunk_run_id uuid;
ALTER TABLE chunk ADD COLUMN structure jsonb NOT NULL DEFAULT '{}';
ALTER TABLE chunk ADD CONSTRAINT chunk_run_parse_fk FOREIGN KEY(chunk_run_id,parse_id) REFERENCES chunk_run(run_id,parse_id);

CREATE FUNCTION chunk_link_same_parse() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE chunk_parse uuid; target_parse uuid;
BEGIN
 SELECT parse_id INTO chunk_parse FROM chunk WHERE chunk_id=NEW.chunk_id;
 IF TG_TABLE_NAME='chunk_context_link' THEN
   SELECT parse_id INTO target_parse FROM content_block WHERE block_id=NEW.target_block_id;
 ELSE
   SELECT parse_id INTO target_parse FROM evidence_anchor WHERE evidence_id=NEW.evidence_id;
 END IF;
 IF chunk_parse IS DISTINCT FROM target_parse THEN
   RAISE EXCEPTION 'Chunk context/evidence must belong to the same parse';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER chunk_context_parse_guard BEFORE INSERT ON chunk_context_link FOR EACH ROW EXECUTE FUNCTION chunk_link_same_parse();
CREATE TRIGGER chunk_evidence_parse_guard BEFORE INSERT ON chunk_evidence FOR EACH ROW EXECUTE FUNCTION chunk_link_same_parse();
