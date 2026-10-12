-- Fixed source runs and candidate search indexes. No current/latest pointer.
CREATE TABLE retrieval_generation (
 generation_id uuid PRIMARY KEY,
 processor_version text NOT NULL,
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
 corpus_hash text NOT NULL CHECK(corpus_hash ~ '^[0-9a-f]{64}$'),
 chunk_run_id uuid NOT NULL REFERENCES chunk_run,
 knowledge_run_id uuid NOT NULL REFERENCES extraction_run,
 taxonomy_snapshot_id uuid NOT NULL REFERENCES taxonomy_snapshot,
 version_snapshot_id uuid NOT NULL REFERENCES version_reference_snapshot,
 payload jsonb NOT NULL,
 source_count integer NOT NULL CHECK(source_count>0),
 record_count integer NOT NULL CHECK(record_count>=0),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(processor_version,input_hash),
 CHECK((payload->>'schema_version'='retrieval-corpus-1') IS TRUE),
 CHECK((payload->>'generation_id'=generation_id::text) IS TRUE),
 CHECK((payload->>'processor_version'=processor_version) IS TRUE),
 CHECK((payload->>'input_hash'=input_hash) IS TRUE),
 CHECK((payload->>'review_state'='review_pending') IS TRUE),
 CHECK((payload->'executable'='false'::jsonb) IS TRUE),
 CHECK((payload->'scope_complete'='false'::jsonb) IS TRUE),
 CHECK((jsonb_array_length(payload->'sources')=source_count) IS TRUE),
 CHECK((jsonb_array_length(payload->'record_manifest')=record_count) IS TRUE),
 CHECK((payload->'source_snapshots'->>'chunk_run_id'=chunk_run_id::text) IS TRUE),
 CHECK((payload->'source_snapshots'->>'knowledge_run_id'=knowledge_run_id::text) IS TRUE),
 CHECK((payload->'source_snapshots'->>'taxonomy_snapshot_id'=taxonomy_snapshot_id::text) IS TRUE),
 CHECK((payload->'source_snapshots'->>'version_snapshot_id'=version_snapshot_id::text) IS TRUE)
);
CREATE TABLE retrieval_generation_source (
 generation_id uuid NOT NULL REFERENCES retrieval_generation,
 source_key text NOT NULL CHECK(length(source_key)>0),
 asset_id text NOT NULL REFERENCES source_asset,
 PRIMARY KEY(generation_id,source_key)
);
CREATE TABLE retrieval_record (
 generation_id uuid NOT NULL REFERENCES retrieval_generation,
 record_id uuid NOT NULL,
 record_type text NOT NULL,
 payload jsonb NOT NULL,
 payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
 index_hash text NOT NULL CHECK(index_hash ~ '^[0-9a-f]{64}$'),
 exact_terms jsonb NOT NULL CHECK(jsonb_typeof(exact_terms)='object'),
 raw_text text NOT NULL,
 normalized_text text NOT NULL,
 tokens tsvector NOT NULL,
 PRIMARY KEY(generation_id,record_id),
 CHECK((payload->>'record_id'=record_id::text) IS TRUE),
 CHECK((payload->>'record_type'=record_type) IS TRUE),
 CHECK((payload->>'review_state' IN ('extracted','machine_checked','review_pending','approved','rejected')) IS TRUE),
 CHECK((payload->'executable'='false'::jsonb) IS TRUE)
);
CREATE INDEX retrieval_record_tokens_gin ON retrieval_record USING gin(tokens);
CREATE INDEX retrieval_record_exact_gin ON retrieval_record USING gin(exact_terms);
CREATE INDEX retrieval_record_kind ON retrieval_record(generation_id,record_type);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON retrieval_generation FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON retrieval_generation_source FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON retrieval_record FOR EACH ROW EXECUTE FUNCTION immutable_row();

CREATE FUNCTION guard_retrieval_members() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE gid uuid; expected_count integer; actual_count bigint; first_key text; expected jsonb; actual jsonb;
BEGIN
 gid := NEW.generation_id;
 IF TG_TABLE_NAME='retrieval_record' THEN
  SELECT record_count INTO expected_count FROM retrieval_generation WHERE generation_id=gid;
  SELECT count(*),min(record_id::text) INTO actual_count,first_key FROM retrieval_record WHERE generation_id=gid;
  IF expected_count IS DISTINCT FROM actual_count THEN RAISE EXCEPTION 'RETRIEVAL_RECORD_MANIFEST_MISMATCH'; END IF;
  IF NEW.record_id::text IS DISTINCT FROM first_key THEN RETURN NULL; END IF;
  SELECT payload->'record_manifest' INTO expected FROM retrieval_generation WHERE generation_id=gid;
  SELECT jsonb_agg(jsonb_build_array(record_id::text,payload_hash,index_hash) ORDER BY record_id::text) INTO actual
   FROM retrieval_record WHERE generation_id=gid;
 ELSE
  SELECT source_count INTO expected_count FROM retrieval_generation WHERE generation_id=gid;
  SELECT count(*),min(source_key) INTO actual_count,first_key FROM retrieval_generation_source WHERE generation_id=gid;
  IF expected_count IS DISTINCT FROM actual_count THEN RAISE EXCEPTION 'RETRIEVAL_SOURCE_MANIFEST_MISMATCH'; END IF;
  IF TG_TABLE_NAME='retrieval_generation_source' THEN
   IF NEW.source_key IS DISTINCT FROM first_key THEN RETURN NULL; END IF;
  END IF;
  SELECT jsonb_agg(jsonb_build_object('source_key',s->>'source_key','asset_id',s->>'asset_id') ORDER BY s->>'source_key') INTO expected
   FROM retrieval_generation g,jsonb_array_elements(g.payload->'sources') s WHERE g.generation_id=gid;
  SELECT jsonb_agg(jsonb_build_object('source_key',source_key,'asset_id',asset_id) ORDER BY source_key) INTO actual
   FROM retrieval_generation_source WHERE generation_id=gid;
 END IF;
 IF coalesce(expected,'[]'::jsonb) IS DISTINCT FROM coalesce(actual,'[]'::jsonb) THEN
  RAISE EXCEPTION 'RETRIEVAL_MANIFEST_IDENTITY_MISMATCH';
 END IF;
 IF TG_TABLE_NAME='retrieval_generation' THEN
  SELECT record_count INTO expected_count FROM retrieval_generation WHERE generation_id=gid;
  SELECT count(*) INTO actual_count FROM retrieval_record WHERE generation_id=gid;
  IF expected_count IS DISTINCT FROM actual_count THEN RAISE EXCEPTION 'RETRIEVAL_RECORD_MANIFEST_MISMATCH'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER retrieval_complete AFTER INSERT ON retrieval_generation
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_retrieval_members();
CREATE CONSTRAINT TRIGGER retrieval_sources_complete AFTER INSERT ON retrieval_generation_source
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_retrieval_members();
CREATE CONSTRAINT TRIGGER retrieval_records_complete AFTER INSERT ON retrieval_record
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_retrieval_members();
