-- Candidate classifications, never a knowledge release or expert approval.
CREATE TABLE taxonomy_snapshot (
 snapshot_id uuid PRIMARY KEY,
 processor_version text NOT NULL,
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(processor_version,input_hash),
 CHECK((payload->>'schema_version'='taxonomy-1') IS TRUE),
 CHECK((payload->>'snapshot_id'=snapshot_id::text) IS TRUE),
 CHECK((payload->>'processor_version'=processor_version) IS TRUE),
 CHECK((payload->>'input_hash'=input_hash) IS TRUE),
 CHECK((payload->>'review_state'='review_pending') IS TRUE),
 CHECK((payload->'executable'='false'::jsonb) IS TRUE),
 CHECK((payload->'scope_complete'='false'::jsonb) IS TRUE),
 CHECK((jsonb_typeof(payload->'sources')='array' AND jsonb_array_length(payload->'sources')>0) IS TRUE),
 CHECK((jsonb_typeof(payload->'namespaces')='array' AND jsonb_array_length(payload->'namespaces')>0) IS TRUE)
);
CREATE TABLE taxonomy_snapshot_source (
 snapshot_id uuid NOT NULL REFERENCES taxonomy_snapshot,
 source_key text NOT NULL CHECK(length(source_key)>0),
 asset_id text NOT NULL REFERENCES source_asset,
 legacy_id uuid REFERENCES legacy_record,
 PRIMARY KEY(snapshot_id,source_key)
);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON taxonomy_snapshot FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON taxonomy_snapshot_source FOR EACH ROW EXECUTE FUNCTION immutable_row();

CREATE FUNCTION guard_taxonomy_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.legacy_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM legacy_record WHERE legacy_id=NEW.legacy_id
   AND index_asset_id=NEW.asset_id AND source_key=NEW.source_key) THEN
  RAISE EXCEPTION 'TAXONOMY_SOURCE_IDENTITY_MISMATCH';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER taxonomy_source_identity BEFORE INSERT ON taxonomy_snapshot_source FOR EACH ROW EXECUTE FUNCTION guard_taxonomy_source();

CREATE FUNCTION guard_taxonomy_snapshot_members() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid uuid; expected jsonb; actual jsonb;
BEGIN
 sid := NEW.snapshot_id;
 SELECT jsonb_agg(jsonb_build_object('source_key',s->>'source_key','asset_id',s->>'asset_id',
     'legacy_id',s->>'legacy_id') ORDER BY s->>'source_key') INTO expected
 FROM taxonomy_snapshot t, jsonb_array_elements(t.payload->'sources') s WHERE t.snapshot_id=sid;
 SELECT jsonb_agg(jsonb_build_object('source_key',source_key,'asset_id',asset_id,
     'legacy_id',legacy_id::text) ORDER BY source_key) INTO actual
 FROM taxonomy_snapshot_source WHERE snapshot_id=sid;
 IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'TAXONOMY_SOURCE_MANIFEST_MISMATCH'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER taxonomy_snapshot_complete AFTER INSERT ON taxonomy_snapshot
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_taxonomy_snapshot_members();
CREATE CONSTRAINT TRIGGER taxonomy_snapshot_sources_complete AFTER INSERT ON taxonomy_snapshot_source
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_taxonomy_snapshot_members();
